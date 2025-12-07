#!/usr/bin/env python3
"""
The Forest Player Data Swap Tool
Swap player data between CS files and __RESUME__ files.

This tool works by:
1. Parsing the Unity Serializer format to identify object boundaries
2. Extracting player-specific objects (inventory, upgrades, etc.)
3. Replacing those objects between files
"""

import base64
import struct
import os
import sys
import re
import shutil
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path
from dataclasses import dataclass


@dataclass
class SerializedObject:
    """Represents a serialized Unity object."""
    start: int
    end: int
    type_name: str
    guid: Optional[str] = None
    data: bytes = None


def read_file(filepath: str) -> bytes:
    with open(filepath, 'rb') as f:
        return f.read()


def write_file(filepath: str, data: bytes):
    with open(filepath, 'wb') as f:
        f.write(data)


def find_all(data: bytes, pattern: bytes) -> List[int]:
    positions = []
    pos = 0
    while True:
        idx = data.find(pattern, pos)
        if idx == -1:
            break
        positions.append(idx)
        pos = idx + 1
    return positions


def decode_resume(filepath: str) -> Tuple[bytes, bytes, bool]:
    """
    Decode __RESUME__ file.
    Returns: (header, inner_data, is_base64)
    """
    raw = read_file(filepath)

    # Check if base64
    is_base64 = all(c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r' for c in raw)

    if is_base64:
        decoded = base64.b64decode(raw)
    else:
        decoded = raw

    # Find NOCOMPRESSION marker
    nocomp_pos = decoded.find(b'NOCOMPRESSION')
    if nocomp_pos == -1:
        raise ValueError("Not a valid __RESUME__ file")

    header = decoded[:nocomp_pos]

    # Decode inner base64
    inner_start = nocomp_pos + len(b'NOCOMPRESSION')
    base64_chars = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
    inner_end = inner_start
    while inner_end < len(decoded) and decoded[inner_end] in base64_chars:
        inner_end += 1

    inner_b64 = decoded[inner_start:inner_end]
    inner = base64.b64decode(inner_b64)

    return header, inner, is_base64


def encode_resume(header: bytes, inner: bytes, is_base64: bool) -> bytes:
    """Encode data back to __RESUME__ format."""
    inner_b64 = base64.b64encode(inner)
    decoded = header + b'NOCOMPRESSION' + inner_b64

    if is_base64:
        return base64.b64encode(decoded)
    return decoded


def parse_level_data_structure(data: bytes) -> Dict[str, Any]:
    """
    Parse the LevelSerializer+LevelData structure.
    This contains the StoredItems, object names, etc.
    """
    result = {}

    # Find the header position
    serv10_pos = data.find(b'\x06SerV10')
    if serv10_pos == -1:
        return result

    pos = serv10_pos + 8  # Skip header + null

    # Read type count
    type_count = struct.unpack('<I', data[pos:pos+4])[0]
    pos += 4

    result['types'] = []
    for _ in range(type_count):
        type_len = data[pos]
        pos += 1
        if type_len & 0x80:
            type_len = (type_len & 0x7F) | (data[pos] << 7)
            pos += 1
        type_name = data[pos:pos+type_len].decode('utf-8', errors='replace')
        pos += type_len
        result['types'].append(type_name)

    result['data_start'] = pos
    return result


def find_object_boundaries(data: bytes) -> List[SerializedObject]:
    """
    Find all serialized object boundaries in the data.
    Objects are marked by SerV10 headers followed by type info.
    """
    objects = []

    # Find all SerV10 markers
    positions = find_all(data, b'\x06SerV10')

    for i, pos in enumerate(positions):
        # Determine end (next SerV10 or end of data)
        if i + 1 < len(positions):
            end = positions[i + 1]
        else:
            end = len(data)

        # Extract the section
        section = data[pos:end]

        # Try to identify the type
        type_name = "Unknown"

        # Look for TheForest type names
        type_patterns = [
            b'TheForest.Items.Inventory.InventoryItemView',
            b'TheForest.Items.Inventory.DecayingInventoryItemView',
            b'TheForest.Items.Craft.UpgradeViewReceiver',
            b'TheForest.Player.',
            b'TheForest.Buildings.',
            b'TheForest.Items.',
            b'UnityEngine.Transform',
        ]

        for pattern in type_patterns:
            if pattern in section:
                # Extract full type name
                idx = section.find(pattern)
                # Find the end of the type name (null or non-printable)
                end_idx = idx
                while end_idx < len(section) and 32 <= section[end_idx] < 127:
                    end_idx += 1
                type_name = section[idx:end_idx].decode('utf-8', errors='replace')
                break

        # Look for GUID
        guid = None
        guid_pattern = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
        guid_match = guid_pattern.search(section)
        if guid_match:
            guid = guid_match.group().decode('ascii')

        obj = SerializedObject(
            start=pos,
            end=end,
            type_name=type_name,
            guid=guid,
            data=section
        )
        objects.append(obj)

    return objects


def is_player_object(obj: SerializedObject) -> bool:
    """Check if an object is player-specific (not world/building)."""
    player_types = [
        'InventoryItemView',
        'DecayingInventoryItemView',
        'UpgradeViewReceiver',
        'SurvivalBookBestiary',
        'ItemStorage',
        'ActiveAreaInfo',
        'HeldItemsData',
        'TickOffSystem',
        'PassengerManifest',
        'TheForest.Player.',
    ]

    for pt in player_types:
        if pt in obj.type_name:
            return True
    return False


def is_world_object(obj: SerializedObject) -> bool:
    """Check if an object is world-specific (buildings, etc.)."""
    world_types = [
        'BuildingHealth',
        'Architect',
        'TreeStructure',
        'Fire2',
        'Cook',
    ]

    for wt in world_types:
        if wt in obj.type_name:
            return True
    return False


def extract_player_objects(data: bytes) -> List[SerializedObject]:
    """Extract all player-related objects from data."""
    objects = find_object_boundaries(data)
    return [obj for obj in objects if is_player_object(obj)]


def extract_world_objects(data: bytes) -> List[SerializedObject]:
    """Extract all world-related objects from data."""
    objects = find_object_boundaries(data)
    return [obj for obj in objects if is_world_object(obj)]


def create_cs_from_resume(resume_path: str, output_path: str):
    """
    Create a CS file from __RESUME__ by extracting player data.
    The CS file will have the same format as client save files.
    """
    print(f"Extracting player data from: {resume_path}")

    header, inner, is_base64 = decode_resume(resume_path)
    print(f"Inner data size: {len(inner):,} bytes")

    # Parse the structure
    structure = parse_level_data_structure(inner)
    print(f"Types in file: {structure.get('types', [])}")

    # Find all objects
    all_objects = find_object_boundaries(inner)
    player_objects = [obj for obj in all_objects if is_player_object(obj)]

    print(f"Total objects: {len(all_objects)}")
    print(f"Player objects: {len(player_objects)}")

    # Group by type
    type_counts = {}
    for obj in player_objects:
        short_name = obj.type_name.split('.')[-1][:30]
        type_counts[short_name] = type_counts.get(short_name, 0) + 1

    print("\nPlayer object types:")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")

    # For CS file format, we need to create a proper header
    # CS files use: SerV10 + LevelData type + object data

    # Get the header portion from the inner data (up to first player object)
    if player_objects:
        first_player_pos = min(obj.start for obj in player_objects)
        cs_header = inner[:structure.get('data_start', first_player_pos)]

        # Combine header with all player object data
        player_data = b''.join(obj.data for obj in player_objects)

        # The CS file needs the header + field definitions + player objects
        # For now, create a raw extraction
        cs_content = inner[:first_player_pos]  # Header + field defs

        # Add player objects
        for obj in player_objects:
            cs_content += obj.data

        write_file(output_path, cs_content)
        print(f"\nCreated CS file: {output_path}")
        print(f"Size: {len(cs_content):,} bytes")

    else:
        print("No player objects found!")


def inject_cs_to_resume(resume_path: str, cs_path: str, output_path: str = None):
    """
    Inject player data from CS file into __RESUME__.
    This replaces the host's player data with data from the CS file.
    """
    if output_path is None:
        output_path = resume_path

    print(f"Loading __RESUME__: {resume_path}")
    header, inner, is_base64 = decode_resume(resume_path)
    inner = bytearray(inner)

    print(f"Loading CS file: {cs_path}")
    cs_data = read_file(cs_path)

    # Find objects in both files
    resume_objects = find_object_boundaries(bytes(inner))
    cs_objects = find_object_boundaries(cs_data)

    resume_player = [obj for obj in resume_objects if is_player_object(obj)]
    cs_player = [obj for obj in cs_objects if is_player_object(obj)]

    print(f"__RESUME__ player objects: {len(resume_player)}")
    print(f"CS player objects: {len(cs_player)}")

    # Strategy: Match objects by type and GUID, then replace data
    # This is a simplified approach

    # Group objects by type
    def group_by_type(objects):
        groups = {}
        for obj in objects:
            key = obj.type_name.split('.')[-1]
            if key not in groups:
                groups[key] = []
            groups[key].append(obj)
        return groups

    resume_groups = group_by_type(resume_player)
    cs_groups = group_by_type(cs_player)

    # For each type present in both, replace __RESUME__ objects with CS objects
    replacements = []

    for type_name in resume_groups:
        if type_name not in cs_groups:
            continue

        resume_objs = resume_groups[type_name]
        cs_objs = cs_groups[type_name]

        print(f"  {type_name}: {len(resume_objs)} in RESUME, {len(cs_objs)} in CS")

        # Match by index (simplified - could match by GUID)
        for i, resume_obj in enumerate(resume_objs):
            if i < len(cs_objs):
                cs_obj = cs_objs[i]
                replacements.append((resume_obj, cs_obj))

    # Apply replacements (from end to start to preserve positions)
    replacements.sort(key=lambda x: x[0].start, reverse=True)

    for resume_obj, cs_obj in replacements:
        # Replace the section
        inner[resume_obj.start:resume_obj.end] = cs_obj.data

    print(f"\nApplied {len(replacements)} replacements")

    # Save
    if output_path != resume_path:
        # Create backup
        if os.path.exists(resume_path):
            backup = resume_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(resume_path, backup)
            print(f"Created backup: {backup}")

    final = encode_resume(header, bytes(inner), is_base64)
    write_file(output_path, final)
    print(f"Saved to: {output_path} ({len(final):,} bytes)")


def compare_files(file1: str, file2: str):
    """Compare player data between two files."""
    print("=" * 60)
    print("COMPARING FILES")
    print("=" * 60)

    # Determine file types and load
    def load_file(filepath):
        if '__RESUME__' in filepath or 'RESUME' in filepath.upper():
            _, inner, _ = decode_resume(filepath)
            return inner
        else:
            return read_file(filepath)

    print(f"\nFile 1: {file1}")
    data1 = load_file(file1)
    objs1 = find_object_boundaries(data1)
    player1 = [obj for obj in objs1 if is_player_object(obj)]

    print(f"  Size: {len(data1):,} bytes")
    print(f"  Total objects: {len(objs1)}")
    print(f"  Player objects: {len(player1)}")

    print(f"\nFile 2: {file2}")
    data2 = load_file(file2)
    objs2 = find_object_boundaries(data2)
    player2 = [obj for obj in objs2 if is_player_object(obj)]

    print(f"  Size: {len(data2):,} bytes")
    print(f"  Total objects: {len(objs2)}")
    print(f"  Player objects: {len(player2)}")

    # Compare object types
    def get_type_counts(objects):
        counts = {}
        for obj in objects:
            key = obj.type_name.split('.')[-1][:30]
            counts[key] = counts.get(key, 0) + 1
        return counts

    counts1 = get_type_counts(player1)
    counts2 = get_type_counts(player2)

    all_types = set(counts1.keys()) | set(counts2.keys())

    print("\nPlayer object comparison:")
    print(f"{'Type':<35} {'File1':>8} {'File2':>8}")
    print("-" * 55)
    for t in sorted(all_types):
        c1 = counts1.get(t, 0)
        c2 = counts2.get(t, 0)
        diff = "" if c1 == c2 else " *"
        print(f"{t:<35} {c1:>8} {c2:>8}{diff}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='The Forest Player Data Swap Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract host player data to CS file
  %(prog)s extract Slot1/__RESUME__ -o host_player.cs

  # Inject CS player data into __RESUME__
  %(prog)s inject Slot1/__RESUME__ -c client_player.cs -o Slot1/__RESUME__new

  # Compare player data between files
  %(prog)s compare Slot1/__RESUME__ cs/client.cs

  # Swap - extract from A, inject into B
  %(prog)s extract SaveA/__RESUME__ -o temp.cs
  %(prog)s inject SaveB/__RESUME__ -c temp.cs
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Extract command
    extract_parser = subparsers.add_parser('extract', help='Extract player data from __RESUME__ to CS file')
    extract_parser.add_argument('resume', help='__RESUME__ file')
    extract_parser.add_argument('-o', '--output', default='extracted_player.cs', help='Output CS file')

    # Inject command
    inject_parser = subparsers.add_parser('inject', help='Inject CS player data into __RESUME__')
    inject_parser.add_argument('resume', help='__RESUME__ file')
    inject_parser.add_argument('-c', '--cs', required=True, help='CS file to inject')
    inject_parser.add_argument('-o', '--output', help='Output file (default: modify in place)')

    # Compare command
    compare_parser = subparsers.add_parser('compare', help='Compare player data between files')
    compare_parser.add_argument('file1', help='First file (__RESUME__ or CS)')
    compare_parser.add_argument('file2', help='Second file (__RESUME__ or CS)')

    args = parser.parse_args()

    if args.command == 'extract':
        create_cs_from_resume(args.resume, args.output)
    elif args.command == 'inject':
        inject_cs_to_resume(args.resume, args.cs, args.output)
    elif args.command == 'compare':
        compare_files(args.file1, args.file2)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
