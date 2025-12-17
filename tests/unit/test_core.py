"""Basic unit tests for core module."""

import pytest


def test_protocol_constants():
    """Test protocol constants."""
    from src.core.protocol import PacketType, CHALLENGE_SIZE, SIGNATURE_SIZE
    
    assert PacketType.VIDEO == 0x01
    assert PacketType.AUDIO == 0x02
    assert PacketType.AUTH_CHALLENGE == 0x10
    assert CHALLENGE_SIZE == 32
    assert SIGNATURE_SIZE == 64


def test_parse_header():
    """Test header parsing."""
    from src.core.protocol import parse_header, create_header, PacketType
    
    # Create a header
    header_bytes = create_header(PacketType.VIDEO, 1024)
    
    # Parse it back
    header = parse_header(header_bytes)
    
    assert header is not None
    assert header.type == PacketType.VIDEO
    assert header.length == 1024


def test_authenticator_init():
    """Test authenticator initialization."""
    from src.core.auth import Authenticator
    
    auth = Authenticator()
    assert not auth.is_key_loaded


def test_aoa_host_init():
    """Test AOA host initialization."""
    from src.core.aoa import AoaHost
    
    host = AoaHost()
    assert not host.is_connected
