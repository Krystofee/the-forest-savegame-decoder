#!/usr/bin/env python3
"""
The Forest Save File Decoder - Full recursive decoder
Handles the nested Unity Serializer format used by The Forest.
"""

import base64
import struct
import json
import os
import sys
import re
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


class BinaryReader:
    """Binary reader with position tracking."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.size = len(data)

    def read(self, n: int) -> bytes:
        if self.pos + n > self.size:
            raise EOFError("End of data")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result

    def read_byte(self) -> int:
        return self.read(1)[0]

    def read_int32(self) -> int:
        return struct.unpack('<i', self.read(4))[0]

    def read_uint32(self) -> int:
        return struct.unpack('<I', self.read(4))[0]

    def read_float(self) -> float:
        return struct.unpack('<f', self.read(4))[0]

    def read_string(self) -> str:
        """Read length-prefixed string."""
        length = self.read_byte()
        if length == 0:
            return ""
        if length & 0x80:
            length = (length & 0x7F) | (self.read_byte() << 7)
        return self.read(length).decode('utf-8', errors='replace')

    def peek(self, n: int = 1) -> bytes:
        return self.data[self.pos:self.pos + n]

    def remaining(self) -> int:
        return self.size - self.pos

    def skip(self, n: int):
        self.pos = min(self.pos + n, self.size)


def decode_all_base64(data: bytes, depth: int = 0) -> bytes:
    """Recursively decode all base64 content."""
    result = data

    # First, try to decode if the entire content is base64
    try:
        if all(c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r' for c in data):
            decoded = base64.b64decode(data)
            if depth < 5:  # Prevent infinite recursion
                return decode_all_base64(decoded, depth + 1)
            return decoded
    except:
        pass

    # Find and decode NOCOMPRESSION sections
    marker = b"NOCOMPRESSION"
    pos = 0
    segments = []
    last_end = 0

    while pos < len(result):
        idx = result.find(marker, pos)
        if idx == -1:
            break

        # Add data before this marker
        segments.append(result[last_end:idx])

        # Find end of base64 data
        start = idx + len(marker)
        end = start
        base64_chars = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
        while end < len(result) and result[end] in base64_chars:
            end += 1

        if end > start:
            b64_data = result[start:end]
            try:
                decoded = base64.b64decode(b64_data)
                # Recursively decode
                if depth < 5:
                    decoded = decode_all_base64(decoded, depth + 1)
                segments.append(decoded)
            except:
                segments.append(result[idx:end])

        last_end = end
        pos = end

    # Add remaining data
    segments.append(result[last_end:])

    return b''.join(segments)


def extract_strings(data: bytes, min_len: int = 4) -> List[str]:
    """Extract printable strings from binary data."""
    strings = []
    current = []

    for byte in data:
        if 32 <= byte < 127:
            current.append(chr(byte))
        else:
            if len(current) >= min_len:
                strings.append(''.join(current))
            current = []

    if len(current) >= min_len:
        strings.append(''.join(current))

    return strings


def find_all(data: bytes, pattern: bytes) -> List[int]:
    """Find all occurrences of a pattern."""
    positions = []
    pos = 0
    while True:
        idx = data.find(pattern, pos)
        if idx == -1:
            break
        positions.append(idx)
        pos = idx + 1
    return positions


def parse_serialized_data(data: bytes) -> Dict[str, Any]:
    """Parse Unity Serializer data structure."""
    result = {
        'buildings': [],
        'items': [],
        'transforms': [],
        'components': [],
        'guids': [],
        'stats': {},
        'strings': []
    }

    # First, fully decode all base64
    decoded = decode_all_base64(data)

    # Extract GUIDs
    guid_pattern = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
    guids = guid_pattern.findall(decoded)
    result['guids'] = list(set(g.decode('ascii') for g in guids))

    # Extract all strings
    result['strings'] = extract_strings(decoded, 5)

    # Find building types
    building_patterns = {
        'BuildingHealth': b'BuildingHealth',
        'WallChunkArchitect': b'WallChunkArchitect',
        'StickFenceChunkArchitect': b'StickFenceChunkArchitect',
        'GardenArchitect': b'GardenArchitect',
        'BridgeArchitect': b'BridgeArchitect',
        'FoundationArchitect': b'FoundationArchitect',
        'TreeStructure': b'TreeStructure',
        'WallDefensiveChunk': b'WallDefensiveChunk',
        'RockFenceChunk': b'RockFenceChunk',
        'WallDefensiveGate': b'WallDefensiveGate',
        'StructureAnchor': b'StructureAnchor',
    }

    for name, pattern in building_patterns.items():
        positions = find_all(decoded, pattern)
        if positions:
            result['buildings'].append({
                'type': name,
                'count': len(positions),
                'positions': positions[:10]  # First 10 positions
            })

    # Find item types
    item_patterns = {
        'Fire': b'Fire2',
        'Cook': b'Cook',
        'UpgradeViewReceiver': b'UpgradeViewReceiver',
    }

    for name, pattern in item_patterns.items():
        positions = find_all(decoded, pattern)
        if positions:
            result['items'].append({
                'type': name,
                'count': len(positions),
            })

    # Find transforms (Unity positions)
    transform_positions = find_all(decoded, b'UnityEngine.Transform')
    result['transforms'] = {
        'count': len(transform_positions)
    }

    # Find stats patterns
    stats_patterns = {
        'Day': b'_day',
        'Trees Cut': b'_treeCutDown',
        'Enemies Killed': b'_enemiesKilled',
        'Structures Built': b'_builtStructures',
        'Items Crafted': b'_itemsCrafted',
        'Arrows Fired': b'_arrowsFired',
    }

    for name, pattern in stats_patterns.items():
        positions = find_all(decoded, pattern)
        if positions:
            result['stats'][name] = {
                'found': True,
                'count': len(positions)
            }

    # Save the fully decoded data for later analysis
    result['_decoded_size'] = len(decoded)
    result['_decoded_data'] = decoded  # For internal use

    return result


def extract_float_at(data: bytes, pos: int) -> Optional[float]:
    """Extract a float value at the given position."""
    try:
        return struct.unpack('<f', data[pos:pos+4])[0]
    except:
        return None


def extract_building_details(data: bytes) -> List[Dict]:
    """Extract detailed building information including health, position, etc."""
    decoded = decode_all_base64(data)
    buildings = []

    # Find BuildingHealth entries
    pattern = b'BuildingHealth'
    positions = find_all(decoded, pattern)

    for pos in positions:
        building = {
            'type': 'BuildingHealth',
            'position': pos,
        }

        # Look for _hp field nearby
        hp_pos = decoded.find(b'_hp', pos, pos + 200)
        if hp_pos != -1:
            # The value should be a few bytes after the field name
            for offset in range(5, 30):
                hp_value = extract_float_at(decoded, hp_pos + offset)
                if hp_value and 0 < hp_value < 10000:  # Reasonable HP range
                    building['hp'] = hp_value
                    break

        # Look for associated GUID
        guid_pattern = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
        context = decoded[max(0, pos-100):pos+300]
        guid_match = guid_pattern.search(context)
        if guid_match:
            building['guid'] = guid_match.group().decode('ascii')

        buildings.append(building)

    return buildings


def main():
    import argparse

    parser = argparse.ArgumentParser(description='The Forest Save File Decoder')
    parser.add_argument('file', help='Save file to decode')
    parser.add_argument('--output', '-o', help='Output JSON file')
    parser.add_argument('--decode-only', '-d', help='Output fully decoded binary to file')
    parser.add_argument('--search', '-s', help='Search for a string in decoded data')
    parser.add_argument('--buildings', '-b', action='store_true', help='Extract detailed building info')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    print(f"Loading: {args.file}")

    with open(args.file, 'rb') as f:
        data = f.read()

    print(f"Raw size: {len(data):,} bytes")

    # Decode if base64
    if all(c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r' for c in data):
        print("File is base64 encoded, decoding...")
        data = base64.b64decode(data)
        print(f"Decoded size: {len(data):,} bytes")

    # Parse the data
    print("\nParsing save file (this may take a moment)...")
    result = parse_serialized_data(data)

    # Output decoded binary if requested
    if args.decode_only:
        decoded = result.get('_decoded_data', data)
        with open(args.decode_only, 'wb') as f:
            f.write(decoded)
        print(f"\nFully decoded data saved to: {args.decode_only}")
        print(f"Decoded size: {len(decoded):,} bytes")

    # Print summary
    print("\n" + "=" * 60)
    print("SAVE FILE ANALYSIS")
    print("=" * 60)
    print(f"Fully decoded size: {result['_decoded_size']:,} bytes")
    print(f"Unique GUIDs found: {len(result['guids'])}")
    print(f"Strings extracted: {len(result['strings'])}")
    print(f"Transform components: {result['transforms']['count']}")

    # Buildings
    print("\n--- BUILDINGS ---")
    total_buildings = 0
    for b in result['buildings']:
        print(f"  {b['type']}: {b['count']}")
        total_buildings += b['count']
    print(f"  TOTAL: {total_buildings}")

    # Items
    print("\n--- ITEMS/OBJECTS ---")
    for i in result['items']:
        print(f"  {i['type']}: {i['count']}")

    # Stats
    if result['stats']:
        print("\n--- GAME STATS ---")
        for name, info in result['stats'].items():
            print(f"  {name}: found ({info['count']} references)")

    # Detailed building info
    if args.buildings:
        print("\n--- DETAILED BUILDING INFO ---")
        buildings = extract_building_details(data)
        print(f"Found {len(buildings)} building health entries")
        for i, b in enumerate(buildings[:20]):
            hp = b.get('hp', 'unknown')
            guid = b.get('guid', 'unknown')[:20] + '...' if b.get('guid') else 'unknown'
            print(f"  [{i}] HP: {hp}, GUID: {guid}")

    # Search
    if args.search:
        print(f"\n--- SEARCH: '{args.search}' ---")
        decoded = result.get('_decoded_data', data)
        search_bytes = args.search.encode('utf-8')
        positions = find_all(decoded, search_bytes)
        print(f"Found {len(positions)} matches")
        for i, pos in enumerate(positions[:20]):
            context_start = max(0, pos - 20)
            context_end = min(len(decoded), pos + len(search_bytes) + 50)
            context = decoded[context_start:context_end]
            context_str = ''.join(chr(c) if 32 <= c < 127 else '.' for c in context)
            print(f"  {pos}: ...{context_str}...")

    # Some interesting strings
    if args.verbose:
        print("\n--- INTERESTING STRINGS ---")
        interesting = [s for s in result['strings'] if any(k in s.lower() for k in
                       ['forest', 'player', 'health', 'build', 'item', 'save', 'wall', 'tree'])]
        for s in interesting[:50]:
            if len(s) > 80:
                s = s[:77] + "..."
            print(f"  {s}")

    # Export JSON
    if args.output:
        export_data = {
            'decoded_size': result['_decoded_size'],
            'guids': result['guids'][:100],
            'buildings': result['buildings'],
            'items': result['items'],
            'transforms': result['transforms'],
            'stats': result['stats'],
            'sample_strings': result['strings'][:200]
        }
        with open(args.output, 'w') as f:
            json.dump(export_data, f, indent=2)
        print(f"\nExported to: {args.output}")


if __name__ == '__main__':
    main()
