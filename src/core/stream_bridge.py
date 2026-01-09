"""
Stream Bridge - USB to MPV data router.

This module reads from the USB AOA interface, strips protocol headers,
and routes video/audio payloads directly to MPV subprocesses.
"""

import threading
from typing import Callable, Optional

from src.core.aoa import AoaHost
from src.core.auth import Authenticator
from src.core.protocol import (
    HEADER_TOTAL_SIZE,
    PacketType,
    ConfigSubtype,
    create_header,
    parse_header,
)
from src.render.mpv_bridge import MPVBridge


class StreamBridge:
    """
    USB to MPV bridge for low-latency streaming.
    
    Architecture:
        USB → Python (header strip) → MPV stdin (H.264) → GPU decode → Display
    
    Python only routes data, all decoding happens in MPV.
    """
    
    def __init__(
        self,
        aoa_host: AoaHost,
        authenticator: Authenticator,
        mpv_path: Optional[str] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize the stream bridge.
        
        Args:
            aoa_host: Initialized AOA USB host
            authenticator: Ed25519 authenticator with loaded key
            mpv_path: Path to mpv.exe (optional, will search if None)
            status_callback: Optional callback for status messages
        """
        self._aoa_host = aoa_host
        self._authenticator = authenticator
        self._status_callback = status_callback
        
        # MPV bridges
        self._video_mpv = MPVBridge(mpv_path=mpv_path)
        self._audio_mpv: Optional[MPVBridge] = None  # Phase 3
        
        # State
        self._running = False
        self._usb_thread: Optional[threading.Thread] = None
        
        # Config storage (SPS/PPS must be sent first)
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._config_sent = False
        
        # Stats
        self._video_packets = 0
        self._audio_packets = 0
        self._flush_interval = 5  # Flush every N video packets
        
        # Callbacks
        self._audio_callback: Optional[Callable[[bytes], None]] = None
        self._config_callback: Optional[Callable[[int, bytes], None]] = None
    
    def set_audio_callback(self, callback: Callable[[bytes], None]):
        """Set callback for audio packets (Phase 3: will use MPV instead)."""
        self._audio_callback = callback
    
    def set_config_callback(self, callback: Callable[[int, bytes], None]):
        """Set callback for config packets (subtype, data)."""
        self._config_callback = callback
    
    def start(self) -> bool:
        """Start the streaming bridge."""
        if self._running:
            return True
        
        # Start video MPV
        if not self._video_mpv.start():
            self._report_status("Failed to start MPV for video")
            return False
        
        self._running = True
        
        # Start USB read thread
        self._usb_thread = threading.Thread(
            target=self._usb_loop,
            name="USB_Bridge",
            daemon=True
        )
        self._usb_thread.start()
        
        self._report_status("Stream bridge started")
        return True
    
    def stop(self):
        """Stop the streaming bridge."""
        self._running = False
        
        # Stop MPV
        self._video_mpv.stop()
        if self._audio_mpv:
            self._audio_mpv.stop()
        
        # Wait for USB thread
        if self._usb_thread and self._usb_thread.is_alive():
            self._usb_thread.join(timeout=1.0)
        
        self._report_status("Stream bridge stopped")
    
    def _usb_loop(self):
        """
        USB read loop - strips headers and routes to MPV.
        
        This is the heart of the bridge. It must:
        1. Read USB data as fast as possible
        2. Parse 5-byte headers
        3. Strip headers (MPV must NOT see them)
        4. Route payloads to appropriate sink
        """
        buffer = bytearray()
        
        while self._running and self._aoa_host.is_connected:
            # Read USB data (16KB chunks for efficiency)
            data = self._aoa_host.read(16384, timeout_ms=50)
            if data is None:
                self._report_status("USB connection lost")
                break
            if len(data) == 0:
                continue
            
            buffer.extend(data)
            
            # Process complete packets
            while len(buffer) >= HEADER_TOTAL_SIZE:
                header = parse_header(bytes(buffer[:HEADER_TOTAL_SIZE]))
                if not header:
                    # Invalid header, skip one byte and try again
                    buffer = buffer[1:]
                    continue
                
                total_size = HEADER_TOTAL_SIZE + header.length
                if len(buffer) < total_size:
                    # Incomplete packet, wait for more data
                    break
                
                # Extract payload (STRIP the 5-byte header!)
                payload = bytes(buffer[HEADER_TOTAL_SIZE:total_size])
                buffer = buffer[total_size:]
                
                # Route by packet type
                self._handle_packet(header.type, payload)
        
        self._running = False
    
    def _handle_packet(self, packet_type: PacketType, payload: bytes):
        """Handle a received packet - route to appropriate sink."""
        
        if packet_type == PacketType.VIDEO:
            self._handle_video(payload)
        
        elif packet_type == PacketType.AUDIO:
            self._handle_audio(payload)
        
        elif packet_type == PacketType.CONFIG:
            self._handle_config(payload)
        
        elif packet_type == PacketType.AUTH_CHALLENGE:
            self._handle_auth(payload)
        
        elif packet_type == PacketType.AUTH_SUCCESS:
            self._report_status("Authentication successful")
        
        elif packet_type == PacketType.AUTH_FAIL:
            self._report_status("Authentication failed")
            self._running = False
    
    def _handle_video(self, payload: bytes):
        """Route video payload to MPV."""
        # Must send SPS/PPS before video frames
        if not self._config_sent:
            self._send_config_to_mpv()
        
        # Ensure start code
        if not payload.startswith(b'\x00\x00\x00\x01') and not payload.startswith(b'\x00\x00\x01'):
            payload = b'\x00\x00\x00\x01' + payload
        
        # Write to MPV stdin
        self._video_mpv.write(payload)
        
        self._video_packets += 1
        
        # Periodic flush for low latency
        if self._video_packets % self._flush_interval == 0:
            self._video_mpv.flush()
        
        # Log progress
        if self._video_packets == 1:
            self._report_status("First video frame sent to MPV")
        elif self._video_packets % 300 == 0:
            print(f"[StreamBridge] Video packets: {self._video_packets}")
    
    def _handle_audio(self, payload: bytes):
        """Route audio payload to audio handler."""
        self._audio_packets += 1
        
        # For now, use callback (Phase 3 will use audio MPV)
        if self._audio_callback:
            self._audio_callback(payload)
    
    def _handle_config(self, payload: bytes):
        """Handle configuration packets (SPS/PPS/AAC)."""
        if len(payload) < 1:
            return
        
        subtype = payload[0]
        config_data = payload[1:]
        
        if subtype == ConfigSubtype.VIDEO_SPS:
            # Add start code if missing
            if not config_data.startswith(b'\x00\x00\x00\x01'):
                config_data = b'\x00\x00\x00\x01' + config_data
            self._sps = config_data
            print(f"[StreamBridge] SPS: {len(config_data)} bytes")
        
        elif subtype == ConfigSubtype.VIDEO_PPS:
            if not config_data.startswith(b'\x00\x00\x00\x01'):
                config_data = b'\x00\x00\x00\x01' + config_data
            self._pps = config_data
            print(f"[StreamBridge] PPS: {len(config_data)} bytes")
            
            # Send config to MPV now that we have both
            if self._sps:
                self._send_config_to_mpv()
        
        # Notify external callback (for audio config, etc.)
        if self._config_callback:
            self._config_callback(subtype, config_data)
    
    def _send_config_to_mpv(self):
        """Send SPS/PPS to MPV to initialize decoder."""
        if self._config_sent or not self._sps or not self._pps:
            return
        
        print("[StreamBridge] Sending SPS/PPS to MPV...")
        self._video_mpv.write(self._sps)
        self._video_mpv.write(self._pps)
        self._video_mpv.flush()
        self._config_sent = True
        print("[StreamBridge] SPS/PPS sent")
    
    def _handle_auth(self, challenge: bytes):
        """Handle authentication challenge."""
        signature = self._authenticator.sign_challenge(challenge)
        if signature:
            response = create_header(PacketType.AUTH_RESPONSE, len(signature)) + signature
            self._aoa_host.write(response)
            self._report_status("Auth response sent")
        else:
            self._report_status(f"Auth failed: {self._authenticator.last_error}")
    
    def _report_status(self, message: str):
        """Report status message."""
        print(f"[StreamBridge] {message}")
        if self._status_callback:
            self._status_callback(message)
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def stats(self) -> dict:
        return {
            'video_packets': self._video_packets,
            'audio_packets': self._audio_packets,
        }
