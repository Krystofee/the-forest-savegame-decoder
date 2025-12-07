#!/usr/bin/env python3
"""
The Forest Player Data Swap Tool v2
Properly handles the Unity Serializer format by preserving the blob structure.

Key insight: The format is:
1. Outer header (version, types, properties)
2. LevelData prefix (contains StoredItems count)
3. A series of SerV10 blobs (each is a complete serialized component)

We treat blobs as opaque and swap them based on their type.
"""

import base64
import struct
import os
import shutil
from datetime import datetime
from typing import List, Tuple, Dict
from dataclasses import dataclass


@dataclass
class Blob:
    """A serialized component blob (starts with SerV10)."""
    offset: int
    data: bytes
    types: List[str]

    @property
    def primary_type(self) -> str:
        return self.types[0] if self.types else "Unknown"

    @property
    def short_type(self) -> str:
        return self.primary_type.split('.')[-1]

    def is_player_data(self) -> bool:
        """Check if this blob contains player-specific data."""
        player_types = [
            'InventoryItemView',
            'DecayingInventoryItemView',
            'MapInventoryItemView',
            'WaterSkinInventoryItemView',
            'RobotInventoryItemView',
            'DrawingsInventoryItemView',
            'UpgradeViewReceiver',
            'SurvivalBookBestiary',
            'SurvivalBookTodo',
            'ItemStorage',
            'ActiveAreaInfo',
            'HeldItemsData',
            'TickOffSystem',
            'PassengerManifest',
            'PlayerClothing',
            'PlayerStats',
            'CaveMapDrawer',
            'WalkmanControler',
            'LogControler',
            'AchievementsManager',
        ]
        for pt in player_types:
            if pt in self.primary_type:
                return True
        return False


def read_7bit_int(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a 7-bit encoded integer, return (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if (b & 0x80) == 0:
            break
        shift += 7
    return result, pos


def write_7bit_int(value: int) -> bytes:
    """Write a 7-bit encoded integer."""
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def parse_header(data: bytes) -> Tuple[int, List[str], List[str]]:
    """
    Parse the outer header and return (data_start, types, properties).
    """
    pos = 0

    # Version string (7-bit length prefix)
    ver_len, pos = read_7bit_int(data, pos)
    version = data[pos:pos + ver_len].decode('utf-8')
    pos += ver_len
    pos += 1  # null separator

    # Type list
    type_count = struct.unpack('<I', data[pos:pos + 4])[0]
    pos += 4
    types = []
    for _ in range(type_count):
        tlen, pos = read_7bit_int(data, pos)
        types.append(data[pos:pos + tlen].decode('utf-8'))
        pos += tlen

    # Property list
    prop_count = struct.unpack('<I', data[pos:pos + 4])[0]
    pos += 4
    properties = []
    for _ in range(prop_count):
        plen, pos = read_7bit_int(data, pos)
        properties.append(data[pos:pos + plen].decode('utf-8'))
        pos += plen

    return pos, types, properties


def find_blobs(data: bytes, start: int) -> List[Blob]:
    """
    Find all SerV10 blobs starting from the given position.
    """
    blobs = []
    serv_pattern = b'\x06SerV10'

    # Find all SerV10 positions after start
    positions = []
    pos = start
    while True:
        idx = data.find(serv_pattern, pos)
        if idx == -1:
            break
        positions.append(idx)
        pos = idx + 1

    # Extract each blob
    for i, blob_start in enumerate(positions):
        # Blob ends at next SerV10 or end of data
        blob_end = positions[i + 1] if i + 1 < len(positions) else len(data)
        blob_data = data[blob_start:blob_end]

        # Parse blob header to get types
        try:
            blob_pos = 0
            ver_len, blob_pos = read_7bit_int(blob_data, blob_pos)
            blob_pos += ver_len + 1  # version + null

            type_count = struct.unpack('<I', blob_data[blob_pos:blob_pos + 4])[0]
            blob_pos += 4

            blob_types = []
            for _ in range(type_count):
                tlen, blob_pos = read_7bit_int(blob_data, blob_pos)
                blob_types.append(blob_data[blob_pos:blob_pos + tlen].decode('utf-8', errors='replace'))
                blob_pos += tlen

            blobs.append(Blob(offset=blob_start, data=blob_data, types=blob_types))
        except Exception as e:
            # Can't parse, store with unknown type
            blobs.append(Blob(offset=blob_start, data=blob_data, types=[f"ParseError: {e}"]))

    return blobs


def decode_resume(filepath: str) -> Tuple[bytes, bytes, bool]:
    """
    Decode __RESUME__ file.
    Returns: (header_with_nocomp, inner_data, is_base64)
    """
    with open(filepath, 'rb') as f:
        raw = f.read()

    # Check if base64
    is_base64 = all(c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r'
                    for c in raw[:1000])

    if is_base64:
        decoded = base64.b64decode(raw)
    else:
        decoded = raw

    nocomp = decoded.find(b'NOCOMPRESSION')
    if nocomp == -1:
        raise ValueError("NOCOMPRESSION marker not found")

    header = decoded[:nocomp + 13]  # Include NOCOMPRESSION
    inner = base64.b64decode(decoded[nocomp + 13:])

    return header, inner, is_base64


def encode_resume(header: bytes, inner: bytes, is_base64: bool) -> bytes:
    """
    Encode data back to __RESUME__ format.
    """
    inner_b64 = base64.b64encode(inner)
    decoded = header + inner_b64

    if is_base64:
        return base64.b64encode(decoded)
    return decoded


def read_cs_file(filepath: str) -> bytes:
    """Read a CS file (raw binary)."""
    with open(filepath, 'rb') as f:
        return f.read()


def analyze_file(filepath: str, file_type: str = "auto") -> Dict:
    """Analyze a save file and return statistics."""
    if file_type == "auto":
        if '__RESUME__' in filepath.upper() or 'RESUME' in filepath:
            file_type = "resume"
        else:
            file_type = "cs"

    if file_type == "resume":
        _, inner, _ = decode_resume(filepath)
    else:
        inner = read_cs_file(filepath)

    data_start, types, properties = parse_header(inner)
    blobs = find_blobs(inner, data_start)

    # Count by type
    type_counts = {}
    player_count = 0
    world_count = 0

    for blob in blobs:
        t = blob.short_type
        type_counts[t] = type_counts.get(t, 0) + 1
        if blob.is_player_data():
            player_count += 1
        else:
            world_count += 1

    return {
        'file_type': file_type,
        'inner_size': len(inner),
        'data_start': data_start,
        'prefix_size': blobs[0].offset - data_start if blobs else 0,
        'blob_count': len(blobs),
        'player_blobs': player_count,
        'world_blobs': world_count,
        'type_counts': type_counts,
        'types': types,
        'properties': properties,
    }


def swap_player_data(resume_path: str, cs_path: str, output_path: str = None):
    """
    Swap player data from CS file into __RESUME__ file.

    Strategy:
    1. Parse both files to extract blobs
    2. Go through RESUME blobs in original order
    3. Replace player blobs with matching CS blobs (by type)
    4. Keep blob count the same (don't change prefix)
    5. Rebuild the file preserving original order
    """
    if output_path is None:
        output_path = resume_path

    print(f"Loading __RESUME__: {resume_path}")
    resume_header, resume_inner, is_base64 = decode_resume(resume_path)
    print(f"  Inner size: {len(resume_inner):,} bytes")

    print(f"Loading CS file: {cs_path}")
    cs_inner = read_cs_file(cs_path)
    print(f"  Size: {len(cs_inner):,} bytes")

    # Parse headers
    resume_data_start, resume_types, resume_props = parse_header(resume_inner)
    cs_data_start, cs_types, cs_props = parse_header(cs_inner)

    # Get prefix (between header and first blob)
    resume_blobs = find_blobs(resume_inner, resume_data_start)
    cs_blobs = find_blobs(cs_inner, cs_data_start)

    print(f"\n__RESUME__: {len(resume_blobs)} blobs")
    print(f"CS file: {len(cs_blobs)} blobs")

    # Separate blobs by type
    resume_player = [b for b in resume_blobs if b.is_player_data()]
    resume_world = [b for b in resume_blobs if not b.is_player_data()]
    cs_player = [b for b in cs_blobs if b.is_player_data()]

    print(f"\n__RESUME__ player blobs: {len(resume_player)}")
    print(f"__RESUME__ world blobs: {len(resume_world)}")
    print(f"CS player blobs: {len(cs_player)}")

    # Show type breakdown
    print("\nPlayer blob types in CS:")
    cs_type_counts = {}
    for b in cs_player:
        t = b.short_type
        cs_type_counts[t] = cs_type_counts.get(t, 0) + 1
    for t, c in sorted(cs_type_counts.items()):
        print(f"  {t}: {c}")

    # Get the outer header and prefix from RESUME
    outer_header = resume_inner[:resume_data_start]
    prefix_end = resume_blobs[0].offset if resume_blobs else len(resume_inner)
    prefix = resume_inner[resume_data_start:prefix_end]

    print(f"\nOuter header size: {len(outer_header)}")
    print(f"Prefix size: {len(prefix)}")

    # Build a map of CS player blobs by type for replacement
    cs_by_type: Dict[str, List[Blob]] = {}
    for b in cs_player:
        t = b.short_type
        if t not in cs_by_type:
            cs_by_type[t] = []
        cs_by_type[t].append(b)

    # Track which CS blobs we've used
    cs_used: Dict[str, int] = {t: 0 for t in cs_by_type}

    # Rebuild blobs preserving original order
    # Replace player blobs with corresponding CS blobs
    new_inner_parts = [outer_header, prefix]  # Keep prefix unchanged
    replacements = 0
    kept_original = 0

    for blob in resume_blobs:
        if blob.is_player_data():
            t = blob.short_type
            if t in cs_by_type and cs_used[t] < len(cs_by_type[t]):
                # Replace with CS blob
                new_inner_parts.append(cs_by_type[t][cs_used[t]].data)
                cs_used[t] += 1
                replacements += 1
            else:
                # No matching CS blob, keep original
                new_inner_parts.append(blob.data)
                kept_original += 1
        else:
            # World blob, keep as is
            new_inner_parts.append(blob.data)

    print(f"\nReplacements: {replacements}")
    print(f"Kept original (no CS match): {kept_original}")

    new_inner = b''.join(new_inner_parts)

    print(f"\nResult:")
    print(f"  Original inner size: {len(resume_inner):,} bytes")
    print(f"  New inner size: {len(new_inner):,} bytes")
    print(f"  Difference: {len(new_inner) - len(resume_inner):+,} bytes")

    # Create backup
    if os.path.exists(output_path):
        backup = output_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(output_path, backup)
        print(f"  Created backup: {backup}")

    # Encode and save
    final = encode_resume(resume_header, new_inner, is_base64)
    with open(output_path, 'wb') as f:
        f.write(final)
    print(f"  Saved to: {output_path} ({len(final):,} bytes)")

    # Verify
    print("\nVerifying...")
    verify_result = analyze_file(output_path, "resume")
    print(f"  Blobs in result: {verify_result['blob_count']}")
    print(f"  Player blobs: {verify_result['player_blobs']}")
    print(f"  World blobs: {verify_result['world_blobs']}")

    return verify_result


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='The Forest Player Data Swap Tool v2',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze files
  %(prog)s analyze Slot1/__RESUME__
  %(prog)s analyze cs/player.cs

  # Swap player data
  %(prog)s swap Slot1/__RESUME__ cs/player.cs -o Slot1/__RESUME__new
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Analyze a save file')
    analyze_parser.add_argument('file', help='File to analyze')
    analyze_parser.add_argument('-t', '--type', choices=['resume', 'cs', 'auto'],
                                default='auto', help='File type')

    # Swap command
    swap_parser = subparsers.add_parser('swap', help='Swap player data from CS into RESUME')
    swap_parser.add_argument('resume', help='__RESUME__ file')
    swap_parser.add_argument('cs', help='CS file with player data')
    swap_parser.add_argument('-o', '--output', help='Output file (default: modify in place)')

    args = parser.parse_args()

    if args.command == 'analyze':
        result = analyze_file(args.file, args.type)
        print(f"File: {args.file}")
        print(f"Type: {result['file_type']}")
        print(f"Inner size: {result['inner_size']:,} bytes")
        print(f"Blobs: {result['blob_count']}")
        print(f"  Player: {result['player_blobs']}")
        print(f"  World: {result['world_blobs']}")
        print("\nBlob types:")
        for t, c in sorted(result['type_counts'].items(), key=lambda x: -x[1])[:20]:
            print(f"  {t}: {c}")

    elif args.command == 'swap':
        swap_player_data(args.resume, args.cs, args.output)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
