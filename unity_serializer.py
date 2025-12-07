#!/usr/bin/env python3
"""
Unity Serializer Format Decoder
Reverse-engineered implementation of the UnitySerializer/SilverlightSerializer binary format.

Format structure:
1. Version header: length-prefixed string "SerV10" + null byte
2. Type count (uint32) + type names (length-prefixed)
3. Property count (uint32) + property names (length-prefixed)
4. Object data with type tokens (ushort) and property tokens (ushort)
"""

import struct
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, BinaryIO
from io import BytesIO


@dataclass
class StoredData:
    """Represents a serialized component."""
    class_id: str
    data: bytes
    name: str
    type_name: str


@dataclass
class StoredItem:
    """Represents a GameObject's metadata."""
    active: bool = True
    layer: int = 0
    tag: str = ""
    set_extra_data: bool = False
    children: Dict[str, List[str]] = field(default_factory=dict)
    class_id: str = ""
    components: Dict[str, bool] = field(default_factory=dict)
    game_object_name: str = ""
    name: str = ""
    parent_name: str = ""
    create_empty_object: bool = False


@dataclass
class LevelData:
    """Root container for serialized level."""
    name: str = ""
    stored_items: List[StoredData] = field(default_factory=list)
    stored_object_names: List[StoredItem] = field(default_factory=list)
    root_object: str = ""


class BinaryReader:
    """Binary reader with Unity Serializer format support."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.types: List[str] = []
        self.properties: List[str] = []

    def read_byte(self) -> int:
        b = self.data[self.pos]
        self.pos += 1
        return b

    def read_bytes(self, count: int) -> bytes:
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def read_int32(self) -> int:
        result = struct.unpack('<i', self.data[self.pos:self.pos + 4])[0]
        self.pos += 4
        return result

    def read_uint32(self) -> int:
        result = struct.unpack('<I', self.data[self.pos:self.pos + 4])[0]
        self.pos += 4
        return result

    def read_uint16(self) -> int:
        result = struct.unpack('<H', self.data[self.pos:self.pos + 2])[0]
        self.pos += 2
        return result

    def read_float(self) -> float:
        result = struct.unpack('<f', self.data[self.pos:self.pos + 4])[0]
        self.pos += 4
        return result

    def read_bool(self) -> bool:
        return self.read_byte() != 0

    def read_7bit_encoded_int(self) -> int:
        """Read a 7-bit encoded integer (variable length)."""
        result = 0
        shift = 0
        while True:
            b = self.read_byte()
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                break
            shift += 7
        return result

    def read_length_prefixed_string(self) -> str:
        """Read a string with 7-bit encoded length prefix."""
        length = self.read_7bit_encoded_int()
        if length == 0:
            return ""
        s = self.data[self.pos:self.pos + length].decode('utf-8', errors='replace')
        self.pos += length
        return s

    def read_header(self) -> str:
        """Read the SerV10 header."""
        version = self.read_length_prefixed_string()
        null_byte = self.read_byte()  # Skip null separator
        return version

    def read_type_list(self) -> List[str]:
        """Read the type registration list."""
        count = self.read_uint32()
        self.types = []
        for _ in range(count):
            self.types.append(self.read_length_prefixed_string())
        return self.types

    def read_property_list(self) -> List[str]:
        """Read the property name list."""
        count = self.read_uint32()
        self.properties = []
        for _ in range(count):
            self.properties.append(self.read_length_prefixed_string())
        return self.properties

    def peek_byte(self) -> int:
        return self.data[self.pos]

    def peek_uint16(self) -> int:
        return struct.unpack('<H', self.data[self.pos:self.pos + 2])[0]

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos


class BinaryWriter:
    """Binary writer with Unity Serializer format support."""

    def __init__(self):
        self.buffer = BytesIO()

    def write_byte(self, value: int):
        self.buffer.write(bytes([value & 0xFF]))

    def write_bytes(self, data: bytes):
        self.buffer.write(data)

    def write_int32(self, value: int):
        self.buffer.write(struct.pack('<i', value))

    def write_uint32(self, value: int):
        self.buffer.write(struct.pack('<I', value))

    def write_uint16(self, value: int):
        self.buffer.write(struct.pack('<H', value))

    def write_float(self, value: float):
        self.buffer.write(struct.pack('<f', value))

    def write_bool(self, value: bool):
        self.write_byte(1 if value else 0)

    def write_7bit_encoded_int(self, value: int):
        """Write a 7-bit encoded integer."""
        while value >= 0x80:
            self.write_byte((value & 0x7F) | 0x80)
            value >>= 7
        self.write_byte(value)

    def write_length_prefixed_string(self, s: str):
        """Write a string with 7-bit encoded length prefix."""
        encoded = s.encode('utf-8')
        self.write_7bit_encoded_int(len(encoded))
        self.buffer.write(encoded)

    def write_header(self, version: str = "SerV10"):
        """Write the SerV10 header."""
        self.write_length_prefixed_string(version)
        self.write_byte(0)  # Null separator

    def write_type_list(self, types: List[str]):
        """Write the type registration list."""
        self.write_uint32(len(types))
        for t in types:
            self.write_length_prefixed_string(t)

    def write_property_list(self, properties: List[str]):
        """Write the property name list."""
        self.write_uint32(len(properties))
        for p in properties:
            self.write_length_prefixed_string(p)

    def get_data(self) -> bytes:
        return self.buffer.getvalue()


class UnitySerializer:
    """
    Deserializer for Unity Serializer format.

    This handles the outer LevelData structure, treating inner StoredData.Data
    as opaque byte blobs (since each has its own serialization context).
    """

    # Known type tokens
    TYPE_NULL = 0xFFFF
    TYPE_REFERENCE = 0xFFFE

    def __init__(self, data: bytes):
        self.reader = BinaryReader(data)
        self.version = ""
        self.types: List[str] = []
        self.properties: List[str] = []
        self.objects: Dict[int, Any] = {}  # object_id -> object
        self.next_object_id = 0

    def deserialize(self) -> LevelData:
        """Deserialize the entire structure."""
        # Read header
        self.version = self.reader.read_header()
        if self.version != "SerV10":
            raise ValueError(f"Unsupported version: {self.version}")

        # Read type and property lists
        self.types = self.reader.read_type_list()
        self.properties = self.reader.read_property_list()

        # Read the LevelData object
        return self._read_level_data()

    def _read_level_data(self) -> LevelData:
        """Read the LevelData structure from the current position."""
        level_data = LevelData()

        # The format here is complex - it uses property tokens and object IDs
        # Let me trace through the actual bytes to understand the structure

        # For now, let's use a simpler approach: find the StoredItems data
        # by looking for patterns in the binary

        # Read until we find a recognizable pattern
        start_pos = self.reader.pos

        # The data after headers seems to follow a specific pattern
        # Let me analyze it more carefully

        return self._parse_level_data_heuristic(start_pos)

    def _parse_level_data_heuristic(self, start_pos: int) -> LevelData:
        """
        Parse LevelData using heuristic pattern matching.

        The structure after headers is:
        - Some prefix bytes (object ID, type info, etc.)
        - Then field data for LevelData
        """
        level_data = LevelData()

        # Get the raw data from start_pos
        data = self.reader.data[start_pos:]

        # Find all nested SerV10 objects - these are the StoredData.Data blobs
        serv10_pattern = b'\x06SerV10'
        positions = []
        pos = 0
        while True:
            idx = data.find(serv10_pattern, pos)
            if idx == -1:
                break
            positions.append(start_pos + idx)
            pos = idx + 1

        # Extract each serialized object
        for i, obj_pos in enumerate(positions):
            # Determine end of this object
            if i + 1 < len(positions):
                end_pos = positions[i + 1]
            else:
                end_pos = len(self.reader.data)

            obj_data = self.reader.data[obj_pos:end_pos]

            # Parse the mini-header to get the type
            mini_reader = BinaryReader(obj_data)
            try:
                mini_version = mini_reader.read_header()
                mini_types = mini_reader.read_type_list()

                type_name = mini_types[0] if mini_types else "Unknown"

                stored = StoredData(
                    class_id="",
                    data=obj_data,
                    name=f"obj_{i}",
                    type_name=type_name
                )
                level_data.stored_items.append(stored)
            except Exception as e:
                # Can't parse, store as raw
                stored = StoredData(
                    class_id="",
                    data=obj_data,
                    name=f"obj_{i}",
                    type_name="ParseError"
                )
                level_data.stored_items.append(stored)

        return level_data

    def get_raw_data_after_headers(self) -> bytes:
        """Get the raw data after the header sections."""
        # Re-read to get to the right position
        reader = BinaryReader(self.reader.data)
        reader.read_header()
        reader.read_type_list()
        reader.read_property_list()
        return reader.data[reader.pos:]


def analyze_file(filepath: str) -> Dict[str, Any]:
    """Analyze a serialized file and return statistics."""
    import base64

    # Read and decode
    with open(filepath, 'rb') as f:
        raw = f.read()

    # Check if base64 encoded
    is_resume = b'NOCOMPRESSION' in raw or all(
        c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r'
        for c in raw[:1000]
    )

    if is_resume:
        # __RESUME__ format
        try:
            decoded = base64.b64decode(raw)
        except:
            decoded = raw

        nocomp = decoded.find(b'NOCOMPRESSION')
        if nocomp != -1:
            inner = base64.b64decode(decoded[nocomp + 13:])
        else:
            inner = decoded
    else:
        # CS file format (raw)
        inner = raw

    # Parse
    serializer = UnitySerializer(inner)
    level_data = serializer.deserialize()

    # Analyze
    type_counts = {}
    for item in level_data.stored_items:
        t = item.type_name.split('.')[-1] if '.' in item.type_name else item.type_name
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        'raw_size': len(raw),
        'inner_size': len(inner),
        'version': serializer.version,
        'types': serializer.types,
        'properties': serializer.properties,
        'object_count': len(level_data.stored_items),
        'type_counts': type_counts,
    }


def main():
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python unity_serializer.py <file>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"Analyzing: {filepath}")
    print()

    result = analyze_file(filepath)

    print(f"Raw size: {result['raw_size']:,} bytes")
    print(f"Inner size: {result['inner_size']:,} bytes")
    print(f"Version: {result['version']}")
    print(f"Types: {result['types']}")
    print(f"Properties: {result['properties']}")
    print(f"Object count: {result['object_count']}")
    print()
    print("Type distribution:")
    for t, c in sorted(result['type_counts'].items(), key=lambda x: -x[1])[:20]:
        print(f"  {t}: {c}")


if __name__ == '__main__':
    main()
