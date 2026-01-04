"""
Protocol constants and utilities for Wolfkrypt stream protocol.
Matches Android app StreamProtocol.kt
"""

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Tuple


class PacketType(IntEnum):
    """Packet type identifiers."""
    VIDEO = 0x01
    AUDIO = 0x02
    CONFIG = 0x03
    HEARTBEAT = 0x04
    
    # Authentication
    AUTH_CHALLENGE = 0x10
    AUTH_RESPONSE = 0x11
    AUTH_SUCCESS = 0x12
    AUTH_FAIL = 0x13


class ConfigSubtype(IntEnum):
    """Config packet subtypes."""
    VIDEO_SPS = 0x01
    VIDEO_PPS = 0x02
    AUDIO_AAC = 0x03


# Header sizes
HEADER_TYPE_SIZE = 1
HEADER_LENGTH_SIZE = 4
HEADER_TOTAL_SIZE = HEADER_TYPE_SIZE + HEADER_LENGTH_SIZE

# Maximum payload size (64KB)
MAX_PAYLOAD_SIZE = 65536

# USB settings
USB_TIMEOUT_MS = 500
USB_BUFFER_SIZE = 16384

# Authentication constants
CHALLENGE_SIZE = 32
SIGNATURE_SIZE = 64
AUTH_TIMEOUT_MS = 5000


@dataclass
class PacketHeader:
    """Packet header structure."""
    type: PacketType
    length: int


def parse_header(data: bytes) -> Optional[PacketHeader]:
    """Parse a packet header from bytes."""
    if len(data) < HEADER_TOTAL_SIZE:
        return None
    
    packet_type = data[0]
    length = struct.unpack('>I', data[1:5])[0]  # Big-endian uint32
    
    # Sanity check: enforce maximum payload size
    if length > MAX_PAYLOAD_SIZE:
        import logging
        logging.error(
            f"Packet length {length} exceeds maximum allowed {MAX_PAYLOAD_SIZE} – discarding packet"
        )
        return None
    
    import logging
    logging.debug(
        f"Parsed packet header: type={PacketType(packet_type).name}, length={length}"
    )
    
    return PacketHeader(type=PacketType(packet_type), length=length)


def create_header(packet_type: PacketType, length: int) -> bytes:
    """Create a packet header."""
    return bytes([packet_type]) + struct.pack('>I', length)


def parse_length(data: bytes) -> int:
    """Parse length from big-endian bytes."""
    return struct.unpack('>I', data)[0]


def write_length(length: int) -> bytes:
    """Write length as big-endian bytes."""
    return struct.pack('>I', length)
