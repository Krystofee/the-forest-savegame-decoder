#!/usr/bin/env python3
"""
The Forest Save File Editor
Full-featured editor for The Forest save files with read/write capability.
"""

import base64
import struct
import json
import os
import sys
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


def decode_base64_file(filepath: str) -> Tuple[bytes, bool]:
    """Read and decode a potentially base64-encoded file."""
    with open(filepath, 'rb') as f:
        data = f.read()

    is_base64 = all(c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r' for c in data)

    if is_base64:
        return base64.b64decode(data), True
    return data, False


def decode_nested_base64(data: bytes, depth: int = 0) -> bytes:
    """Recursively decode all nested base64 content."""
    if depth > 5:
        return data

    # First check if entire content is base64
    try:
        if all(c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r' for c in data):
            decoded = base64.b64decode(data)
            return decode_nested_base64(decoded, depth + 1)
    except:
        pass

    # Find and decode NOCOMPRESSION sections
    marker = b"NOCOMPRESSION"
    result = bytearray()
    pos = 0

    while pos < len(data):
        idx = data.find(marker, pos)
        if idx == -1:
            result.extend(data[pos:])
            break

        result.extend(data[pos:idx])

        # Find end of base64 data
        start = idx + len(marker)
        end = start
        base64_chars = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
        while end < len(data) and data[end] in base64_chars:
            end += 1

        if end > start:
            b64_data = data[start:end]
            try:
                decoded = base64.b64decode(b64_data)
                decoded = decode_nested_base64(decoded, depth + 1)
                result.extend(decoded)
            except:
                result.extend(data[idx:end])
        else:
            result.extend(data[idx:idx + len(marker)])

        pos = end

    return bytes(result)


def encode_base64_with_nocompression(data: bytes) -> bytes:
    """Encode data as base64 with NOCOMPRESSION prefix."""
    encoded = base64.b64encode(data)
    return b"NOCOMPRESSION" + encoded


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


def read_float(data: bytes, pos: int) -> float:
    """Read a float from data at position."""
    return struct.unpack('<f', data[pos:pos+4])[0]


def write_float(data: bytearray, pos: int, value: float):
    """Write a float to data at position."""
    data[pos:pos+4] = struct.pack('<f', value)


def read_int32(data: bytes, pos: int) -> int:
    """Read an int32 from data at position."""
    return struct.unpack('<i', data[pos:pos+4])[0]


def write_int32(data: bytearray, pos: int, value: int):
    """Write an int32 to data at position."""
    data[pos:pos+4] = struct.pack('<i', value)


class BuildingHealth:
    """Represents a building health entry."""

    def __init__(self, position: int, hp: float, guid: str = None):
        self.position = position
        self.hp = hp
        self.guid = guid
        self.hp_offset = None  # Offset within the structure where HP is stored

    def __repr__(self):
        return f"BuildingHealth(pos={self.position}, hp={self.hp:.1f}, guid={self.guid[:20] if self.guid else 'None'}...)"


class ForestSaveEditor:
    """Editor for The Forest save files."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.raw_data = None
        self.decoded_data = None
        self.fully_decoded = None
        self.is_base64 = False
        self.buildings = []
        self.modified = False

        self._load()

    def _load(self):
        """Load the save file."""
        print(f"Loading: {self.filepath}")

        with open(self.filepath, 'rb') as f:
            self.raw_data = f.read()

        print(f"Raw size: {len(self.raw_data):,} bytes")

        # Decode base64 if needed
        self.decoded_data, self.is_base64 = decode_base64_file(self.filepath)
        if self.is_base64:
            print(f"Decoded from base64: {len(self.decoded_data):,} bytes")

        # Fully decode nested base64
        self.fully_decoded = bytearray(decode_nested_base64(self.decoded_data))
        print(f"Fully decoded: {len(self.fully_decoded):,} bytes")

        # Parse building health entries
        self._parse_buildings()

    def _parse_buildings(self):
        """Parse all BuildingHealth entries."""
        # Pattern for BuildingHealth structure
        # The structure is: [length][type name][field count][field names]...[values]
        pattern = b'BuildingHealth'
        positions = find_all(self.fully_decoded, pattern)

        self.buildings = []

        for pos in positions:
            try:
                # Look for _hp field nearby
                context_start = max(0, pos - 50)
                context = self.fully_decoded[context_start:pos + 200]

                # Find _hp in context
                hp_idx = context.find(b'_hp')
                if hp_idx == -1:
                    continue

                # The HP value is typically stored after the field definitions
                # Look for a float value pattern after _hp
                hp_start = context_start + hp_idx

                # Find the actual HP value - it's stored in a specific format
                # Look for the pattern: O\x04\x00\x00\xff\xff followed by float
                value_pattern = b'\x4f\x04\x00\x00\xff\xff'
                val_idx = self.fully_decoded.find(value_pattern, hp_start, hp_start + 100)

                if val_idx != -1:
                    hp_pos = val_idx + len(value_pattern)
                    hp_value = read_float(self.fully_decoded, hp_pos)

                    # Validate HP is in reasonable range
                    if 0 < hp_value < 100000:
                        # Find associated GUID
                        guid = None
                        guid_pattern = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
                        guid_match = guid_pattern.search(self.fully_decoded[pos:pos+300])
                        if guid_match:
                            guid = guid_match.group().decode('ascii')

                        building = BuildingHealth(pos, hp_value, guid)
                        building.hp_offset = hp_pos
                        self.buildings.append(building)
            except:
                pass

        print(f"Found {len(self.buildings)} building health entries")

    def list_buildings(self, limit: int = 50):
        """List building health entries."""
        print(f"\n{'='*60}")
        print("BUILDING HEALTH ENTRIES")
        print(f"{'='*60}")
        print(f"Total: {len(self.buildings)}")
        print()

        # Group by HP value
        hp_groups = {}
        for b in self.buildings:
            hp_key = f"{b.hp:.0f}"
            if hp_key not in hp_groups:
                hp_groups[hp_key] = 0
            hp_groups[hp_key] += 1

        print("HP Distribution:")
        for hp, count in sorted(hp_groups.items(), key=lambda x: float(x[0])):
            print(f"  HP {hp}: {count} buildings")

        print(f"\nFirst {min(limit, len(self.buildings))} entries:")
        for i, b in enumerate(self.buildings[:limit]):
            print(f"  [{i:4d}] HP: {b.hp:7.1f}  Pos: {b.position:8d}  GUID: {b.guid[:20] if b.guid else 'N/A'}...")

    def set_building_hp(self, index: int, new_hp: float):
        """Set the HP of a specific building."""
        if index < 0 or index >= len(self.buildings):
            print(f"Error: Invalid building index {index}")
            return False

        building = self.buildings[index]
        if building.hp_offset is None:
            print(f"Error: Cannot find HP offset for building {index}")
            return False

        old_hp = building.hp
        write_float(self.fully_decoded, building.hp_offset, new_hp)
        building.hp = new_hp
        self.modified = True

        print(f"Building [{index}]: HP changed from {old_hp:.1f} to {new_hp:.1f}")
        return True

    def set_all_building_hp(self, new_hp: float):
        """Set HP for all buildings."""
        count = 0
        for i, building in enumerate(self.buildings):
            if building.hp_offset is not None:
                write_float(self.fully_decoded, building.hp_offset, new_hp)
                building.hp = new_hp
                count += 1

        self.modified = True
        print(f"Set HP to {new_hp:.1f} for {count} buildings")
        return count

    def heal_all_buildings(self):
        """Set all buildings to max HP (typically 200 or 400)."""
        # Find the max HP value currently in use
        if self.buildings:
            max_hp = max(b.hp for b in self.buildings)
            # Round up to nearest common max (50, 100, 200, 400)
            for common_max in [50, 100, 200, 400, 500]:
                if max_hp <= common_max:
                    max_hp = common_max
                    break
        else:
            max_hp = 200

        return self.set_all_building_hp(max_hp)

    def repair_damaged_buildings(self, threshold: float = 50.0, new_hp: float = None):
        """Repair buildings that have HP below threshold."""
        if new_hp is None:
            # Find typical max HP
            max_hp = max(b.hp for b in self.buildings) if self.buildings else 200
            new_hp = max_hp

        count = 0
        for building in self.buildings:
            if building.hp < threshold and building.hp_offset is not None:
                write_float(self.fully_decoded, building.hp_offset, new_hp)
                building.hp = new_hp
                count += 1

        if count > 0:
            self.modified = True
        print(f"Repaired {count} buildings (HP < {threshold}) to {new_hp:.1f}")
        return count

    def search(self, query: str):
        """Search for a string in the decoded data."""
        query_bytes = query.encode('utf-8')
        positions = find_all(self.fully_decoded, query_bytes)

        print(f"\n{'='*60}")
        print(f"SEARCH: '{query}'")
        print(f"{'='*60}")
        print(f"Found {len(positions)} matches")

        for i, pos in enumerate(positions[:30]):
            context_start = max(0, pos - 20)
            context_end = min(len(self.fully_decoded), pos + len(query) + 50)
            context = self.fully_decoded[context_start:context_end]
            context_str = ''.join(chr(c) if 32 <= c < 127 else '.' for c in context)
            print(f"  [{i}] {pos}: ...{context_str}...")

    def save(self, output_path: str = None, backup: bool = True):
        """Save the modified save file."""
        if not self.modified:
            print("No modifications to save")
            return

        if output_path is None:
            output_path = self.filepath

        # Create backup
        if backup and os.path.exists(output_path):
            backup_path = output_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(output_path, backup_path)
            print(f"Created backup: {backup_path}")

        # Rebuild the file
        # The fully_decoded data needs to be re-encoded back to the original format

        # First, encode the inner data as base64 with NOCOMPRESSION
        # Find where the NOCOMPRESSION section starts in the original decoded data
        nocomp_pos = self.decoded_data.find(b'NOCOMPRESSION')

        if nocomp_pos != -1:
            # We need to re-encode the fully decoded data and put it back
            # The structure is: [header][NOCOMPRESSION][base64 data]

            # Get the header (everything before NOCOMPRESSION)
            header = self.decoded_data[:nocomp_pos]

            # Encode the fully decoded data as base64
            inner_encoded = base64.b64encode(bytes(self.fully_decoded))

            # Combine: header + NOCOMPRESSION + encoded data
            new_decoded = header + b'NOCOMPRESSION' + inner_encoded

            # If original file was base64, encode the whole thing
            if self.is_base64:
                final_data = base64.b64encode(new_decoded)
            else:
                final_data = new_decoded

            # Write to file
            with open(output_path, 'wb') as f:
                f.write(final_data)

            print(f"Saved to: {output_path}")
            print(f"New file size: {len(final_data):,} bytes")
            self.modified = False
        else:
            print("Error: Could not find NOCOMPRESSION marker in decoded data")
            print("Save operation failed")

    def export_json(self, output_path: str):
        """Export save data to JSON for analysis."""
        data = {
            'file': self.filepath,
            'raw_size': len(self.raw_data),
            'decoded_size': len(self.decoded_data),
            'fully_decoded_size': len(self.fully_decoded),
            'is_base64': self.is_base64,
            'buildings': [
                {
                    'index': i,
                    'hp': b.hp,
                    'position': b.position,
                    'guid': b.guid
                }
                for i, b in enumerate(self.buildings)
            ]
        }

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"Exported to: {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='The Forest Save File Editor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s Slot1/__RESUME__ --list                    # List buildings
  %(prog)s Slot1/__RESUME__ --heal-all                # Max HP for all buildings
  %(prog)s Slot1/__RESUME__ --repair 50               # Repair buildings < 50 HP
  %(prog)s Slot1/__RESUME__ --set-hp 0 500            # Set building #0 to 500 HP
  %(prog)s Slot1/__RESUME__ --set-all-hp 999          # Set all buildings to 999 HP
  %(prog)s Slot1/__RESUME__ --search "player"         # Search for "player"
        """
    )

    parser.add_argument('file', help='Save file to edit')
    parser.add_argument('--list', '-l', action='store_true', help='List building health entries')
    parser.add_argument('--heal-all', action='store_true', help='Set all buildings to max HP')
    parser.add_argument('--repair', type=float, metavar='THRESHOLD',
                        help='Repair buildings with HP below threshold')
    parser.add_argument('--set-hp', nargs=2, type=float, metavar=('INDEX', 'HP'),
                        help='Set HP of specific building')
    parser.add_argument('--set-all-hp', type=float, metavar='HP',
                        help='Set HP of all buildings')
    parser.add_argument('--search', '-s', type=str, help='Search for a string')
    parser.add_argument('--save', action='store_true', help='Save modifications')
    parser.add_argument('--output', '-o', type=str, help='Output file (default: overwrite original)')
    parser.add_argument('--no-backup', action='store_true', help='Don\'t create backup')
    parser.add_argument('--export', '-e', type=str, help='Export data to JSON')

    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    editor = ForestSaveEditor(args.file)

    if args.list:
        editor.list_buildings()

    if args.heal_all:
        editor.heal_all_buildings()

    if args.repair is not None:
        editor.repair_damaged_buildings(threshold=args.repair)

    if args.set_hp:
        index, hp = args.set_hp
        editor.set_building_hp(int(index), hp)

    if args.set_all_hp:
        editor.set_all_building_hp(args.set_all_hp)

    if args.search:
        editor.search(args.search)

    if args.export:
        editor.export_json(args.export)

    if args.save and editor.modified:
        editor.save(output_path=args.output, backup=not args.no_backup)
    elif editor.modified:
        print("\nModifications made but not saved. Use --save to save changes.")


if __name__ == '__main__':
    main()
