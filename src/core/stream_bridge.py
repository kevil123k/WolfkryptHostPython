"""
Stream Bridge - Optimized USB to MPV data router.
High-throughput version with minimal Python overhead.
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
    """Optimized USB to MPV bridge."""
    
    # Performance tuning
    USB_READ_SIZE = 65536
    USB_TIMEOUT_MS = 100
    FLUSH_INTERVAL = 1
    
    def __init__(
        self,
        aoa_host: AoaHost,
        authenticator: Authenticator,
        mpv_path: Optional[str] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self._aoa_host = aoa_host
        self._authenticator = authenticator
        self._status_callback = status_callback
        
        self._video_player = MPVBridge(mpv_path=mpv_path)
        
        self._running = False
        self._usb_thread: Optional[threading.Thread] = None
        
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._config_sent = False
        
        self._video_packets = 0
        self._bytes_received = 0
        
        self._audio_callback: Optional[Callable[[bytes], None]] = None
        self._config_callback: Optional[Callable[[int, bytes], None]] = None
    
    def set_audio_callback(self, callback: Callable[[bytes], None]):
        self._audio_callback = callback
    
    def set_config_callback(self, callback: Callable[[int, bytes], None]):
        self._config_callback = callback
    
    def start(self) -> bool:
        if self._running:
            return True
        
        if not self._video_player.start():
            self._report_status("Failed to start MPV")
            return False
        
        self._running = True
        
        self._usb_thread = threading.Thread(
            target=self._usb_loop_optimized,
            name="USB_Bridge",
            daemon=True
        )
        self._usb_thread.start()
        
        self._report_status("Stream bridge started")
        return True
    
    def stop(self):
        self._running = False
        self._video_player.stop()
        if self._usb_thread and self._usb_thread.is_alive():
            self._usb_thread.join(timeout=1.0)
        self._report_status("Stream bridge stopped")
    
    def _usb_loop_optimized(self):
        """Optimized USB loop with minimal allocations."""
        
        # Pre-allocate buffer
        buffer = bytearray(self.USB_READ_SIZE * 4)
        buffer_len = 0
        
        # Pre-compute constants
        header_size = HEADER_TOTAL_SIZE
        video_type = PacketType.VIDEO
        audio_type = PacketType.AUDIO
        config_type = PacketType.CONFIG
        auth_challenge = PacketType.AUTH_CHALLENGE
        auth_success = PacketType.AUTH_SUCCESS
        auth_fail = PacketType.AUTH_FAIL
        
        # Start code for video
        start_code = b'\x00\x00\x00\x01'
        
        while self._running and self._aoa_host.is_connected:
            # Large USB read
            data = self._aoa_host.read(self.USB_READ_SIZE, timeout_ms=self.USB_TIMEOUT_MS)
            
            if data is None:
                self._report_status("USB disconnected")
                break
            
            if len(data) == 0:
                continue
            
            self._bytes_received += len(data)
            
            # Append to buffer using memoryview where possible
            data_len = len(data)
            if buffer_len + data_len > len(buffer):
                # Grow buffer if needed
                buffer.extend(b'\x00' * (buffer_len + data_len - len(buffer)))
            buffer[buffer_len:buffer_len + data_len] = data
            buffer_len += data_len
            
            # Process packets
            pos = 0
            while pos + header_size <= buffer_len:
                # Parse header inline (avoid function call)
                header_bytes = buffer[pos:pos + header_size]
                pkt_type = header_bytes[0]
                pkt_len = (header_bytes[1] << 24) | (header_bytes[2] << 16) | (header_bytes[3] << 8) | header_bytes[4]
                
                total = header_size + pkt_len
                if pos + total > buffer_len:
                    break
                
                # Extract payload
                payload_start = pos + header_size
                payload_end = pos + total
                
                # Route packet
                if pkt_type == video_type:
                    # CRITICAL: Drop video until config is sent
                    if not self._config_sent:
                        if self._sps and self._pps:
                            self._send_config_to_mpv()
                        else:
                            # Skip video frame - no config yet
                            pos = pos + total
                            continue
                    
                    payload = bytes(buffer[payload_start:payload_end])
                    
                    # Check start code
                    if len(payload) >= 4 and payload[:4] != start_code and payload[:3] != b'\x00\x00\x01':
                        payload = start_code + payload
                    
                    self._video_player.write(payload)
                    self._video_packets += 1
                    
                    # Flush every frame
                    self._video_player.flush()
                    
                    if self._video_packets == 1:
                        self._report_status("First video frame sent")
                
                elif pkt_type == config_type:
                    payload = bytes(buffer[payload_start:payload_end])
                    self._handle_config(payload)
                
                elif pkt_type == audio_type:
                    # Skip audio for now to reduce overhead
                    pass
                
                elif pkt_type == auth_challenge:
                    payload = bytes(buffer[payload_start:payload_end])
                    self._handle_auth(payload)
                
                elif pkt_type == auth_success:
                    self._report_status("Auth successful")
                
                elif pkt_type == auth_fail:
                    self._report_status("Auth failed")
                    self._running = False
                
                pos = pos + total
            
            # Compact buffer
            if pos > 0:
                remaining = buffer_len - pos
                if remaining > 0:
                    buffer[:remaining] = buffer[pos:buffer_len]
                buffer_len = remaining
        
        self._running = False
        print(f"[StreamBridge] Total: {self._video_packets} packets, {self._bytes_received / 1024 / 1024:.1f} MB")
    
    def _handle_config(self, payload: bytes):
        if len(payload) < 1:
            return
        
        subtype = payload[0]
        config_data = payload[1:]
        
        if subtype == ConfigSubtype.VIDEO_SPS:
            if not config_data.startswith(b'\x00\x00\x00\x01'):
                config_data = b'\x00\x00\x00\x01' + config_data
            self._sps = config_data
            print(f"[StreamBridge] SPS: {len(config_data)} bytes")
        
        elif subtype == ConfigSubtype.VIDEO_PPS:
            if not config_data.startswith(b'\x00\x00\x00\x01'):
                config_data = b'\x00\x00\x00\x01' + config_data
            self._pps = config_data
            print(f"[StreamBridge] PPS: {len(config_data)} bytes")
            if self._sps:
                self._send_config_to_mpv()
        
        if self._config_callback:
            self._config_callback(subtype, config_data)
    
    def _send_config_to_mpv(self):
        if self._config_sent or not self._sps or not self._pps:
            return
        
        print("[StreamBridge] Sending SPS/PPS...")
        self._video_player.write(self._sps)
        self._video_player.write(self._pps)
        self._video_player.flush()
        self._config_sent = True
    
    def _handle_auth(self, challenge: bytes):
        signature = self._authenticator.sign_challenge(challenge)
        if signature:
            response = create_header(PacketType.AUTH_RESPONSE, len(signature)) + signature
            self._aoa_host.write(response)
            self._report_status("Auth response sent")
        else:
            self._report_status(f"Auth failed: {self._authenticator.last_error}")
    
    def _report_status(self, message: str):
        print(f"[StreamBridge] {message}")
        if self._status_callback:
            self._status_callback(message)
    
    @property
    def is_running(self) -> bool:
        return self._running
