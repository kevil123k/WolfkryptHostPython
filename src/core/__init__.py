"""Core module for Wolfkrypt - USB protocol, authentication, and packet handling."""

from .aoa import AoaHost
from .auth import Authenticator
from .protocol import (
    PacketType,
    ConfigSubtype,
    PacketHeader,
    parse_header,
    create_header,
    CHALLENGE_SIZE,
    SIGNATURE_SIZE,
    HEADER_TOTAL_SIZE,
)

__all__ = [
    'AoaHost',
    'Authenticator',
    'PacketType',
    'ConfigSubtype',
    'PacketHeader',
    'parse_header',
    'create_header',
    'CHALLENGE_SIZE',
    'SIGNATURE_SIZE',
    'HEADER_TOTAL_SIZE',
]
