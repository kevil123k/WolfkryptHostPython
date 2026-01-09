"""
StreamPipeline - 3-Thread Video Streaming Architecture.

This module implements the low-latency video pipeline with three stages:

Stage A (USB Pump Thread):
    - Reads packets from USB AOA interface
    - Demuxes by packet type (video, audio, auth)
    - Immediately handles auth packets (high priority)
    - Queues video/audio for processing

Stage B (Decoder Thread):
    - Consumes video packets from queue
    - Decodes using PyAV (hardware accelerated)
    - Outputs YUV frames to DroppingQueue

Stage C (Main Thread / SDL Renderer):
    - Polls DroppingQueue for latest frame
    - Updates SDL2 texture (GPU upload)
    - Renders at display refresh rate
"""

import queue
import threading
from typing import Callable, Optional

from src.core.aoa import AoaHost
from src.core.auth import Authenticator
from src.core.dropping_queue import DroppingQueue
from src.core.protocol import (
    HEADER_TOTAL_SIZE,
    PacketType,
    ConfigSubtype,
    create_header,
    parse_header,
)
from src.media.pyav_decoder import PyAVDecoder, YUVFrame
from src.render.sdl_video import SDLVideoWindow


class StreamPipeline:
    """
    3-thread pipeline for low-latency video streaming.
    
    Architecture:
        USB Thread → VideoPacketQueue → Decoder Thread → DroppingQueue → SDL (Main Thread)
    """
    
    def __init__(
        self,
        aoa_host: AoaHost,
        authenticator: Authenticator,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize the streaming pipeline.
        
        Args:
            aoa_host: Initialized AOA USB host
            authenticator: Ed25519 authenticator with loaded key
            status_callback: Optional callback for status messages
        """
        self._aoa_host = aoa_host
        self._authenticator = authenticator
        self._status_callback = status_callback
        
        # State
        self._running = False
        
        # Queues
        self._video_queue: queue.Queue = queue.Queue(maxsize=30)  # Raw H.264 packets
        self._audio_queue: queue.Queue = queue.Queue(maxsize=50)  # AAC packets
        self._frame_queue: DroppingQueue[YUVFrame] = DroppingQueue(maxsize=1)  # Decoded frames
        
        # Components
        self._video_decoder = PyAVDecoder()
        self._sdl_window: Optional[SDLVideoWindow] = None
        
        # Threads
        self._usb_thread: Optional[threading.Thread] = None
        self._decoder_thread: Optional[threading.Thread] = None
        
        # Callbacks
        self._audio_callback: Optional[Callable[[bytes], None]] = None
        self._config_callback: Optional[Callable[[int, bytes], None]] = None
        
    def set_audio_callback(self, callback: Callable[[bytes], None]):
        """Set callback for audio packets."""
        self._audio_callback = callback
        
    def set_config_callback(self, callback: Callable[[int, bytes], None]):
        """Set callback for config packets (subtype, data)."""
        self._config_callback = callback
        
    def start(self) -> bool:
        """Start the streaming pipeline."""
        if self._running:
            return True
        
        self._running = True
        
        # Start SDL window
        self._sdl_window = SDLVideoWindow(title="Wolfkrypt Mirror")
        if not self._sdl_window.start():
            self._report_status("Failed to start SDL window")
            self._running = False
            return False
        
        # Set up decoder resolution callback
        self._video_decoder.set_resolution_callback(self._on_resolution_change)
        
        # Start USB pump thread (Stage A)
        self._usb_thread = threading.Thread(
            target=self._usb_pump_loop,
            name="USB_Pump",
            daemon=True
        )
        self._usb_thread.start()
        
        # Start decoder thread (Stage B)
        self._decoder_thread = threading.Thread(
            target=self._decoder_loop,
            name="Video_Decoder",
            daemon=True
        )
        self._decoder_thread.start()
        
        # Start frame render timer (Stage C is on main thread, driven by SDL)
        self._start_render_polling()
        
        self._report_status("Pipeline started")
        return True
    
    def stop(self):
        """Stop the streaming pipeline."""
        self._running = False
        
        # Stop decoder
        self._video_decoder.stop()
        
        # Stop SDL window
        if self._sdl_window:
            self._sdl_window.stop()
            self._sdl_window = None
        
        # Clear queues
        self._video_queue = queue.Queue(maxsize=30)
        self._audio_queue = queue.Queue(maxsize=50)
        self._frame_queue.clear()
        
        # Wait for threads
        if self._usb_thread and self._usb_thread.is_alive():
            self._usb_thread.join(timeout=1.0)
        if self._decoder_thread and self._decoder_thread.is_alive():
            self._decoder_thread.join(timeout=1.0)
        
        self._report_status("Pipeline stopped")
    
    def _usb_pump_loop(self):
        """
        Stage A: USB Pump Thread.
        
        Reads data from USB as fast as possible, demuxes packets by type.
        Auth packets are handled immediately (high priority).
        """
        buffer = bytearray()
        
        while self._running and self._aoa_host.is_connected:
            # Read USB data (non-blocking with short timeout)
            data = self._aoa_host.read(16384, timeout_ms=50)
            if data is None:
                # Connection error
                self._report_status("USB connection lost")
                break
            if len(data) == 0:
                continue
            
            buffer.extend(data)
            
            # Process complete packets
            while len(buffer) >= HEADER_TOTAL_SIZE:
                header = parse_header(bytes(buffer[:HEADER_TOTAL_SIZE]))
                if not header:
                    # Invalid header, skip one byte
                    buffer = buffer[1:]
                    continue
                
                total_size = HEADER_TOTAL_SIZE + header.length
                if len(buffer) < total_size:
                    # Incomplete packet, wait for more data
                    break
                
                # Extract payload
                payload = bytes(buffer[HEADER_TOTAL_SIZE:total_size])
                buffer = buffer[total_size:]
                
                # Demux by packet type
                self._handle_packet(header.type, payload)
        
        self._running = False
    
    def _handle_packet(self, packet_type: PacketType, payload: bytes):
        """Handle a received packet based on its type."""
        
        if packet_type == PacketType.VIDEO:
            # Queue for decoder thread
            try:
                self._video_queue.put_nowait(payload)
            except queue.Full:
                pass  # Drop frame if queue full
        
        elif packet_type == PacketType.AUDIO:
            # Queue for audio processing
            try:
                self._audio_queue.put_nowait(payload)
            except queue.Full:
                pass
            
            # Notify audio callback
            if self._audio_callback:
                try:
                    audio_data = self._audio_queue.get_nowait()
                    self._audio_callback(audio_data)
                except queue.Empty:
                    pass
        
        elif packet_type == PacketType.CONFIG:
            # Handle config packets (SPS/PPS/AAC config)
            if len(payload) < 1:
                return
            subtype = payload[0]
            config_data = payload[1:]
            
            if subtype == ConfigSubtype.VIDEO_SPS:
                self._video_decoder.set_sps(config_data)
            elif subtype == ConfigSubtype.VIDEO_PPS:
                self._video_decoder.set_pps(config_data)
            
            # Also notify config callback
            if self._config_callback:
                self._config_callback(subtype, config_data)
        
        elif packet_type == PacketType.AUTH_CHALLENGE:
            # IMMEDIATE: Handle auth challenge (high priority)
            signature = self._authenticator.sign_challenge(payload)
            if signature:
                response = create_header(PacketType.AUTH_RESPONSE, len(signature)) + signature
                self._aoa_host.write(response)
                self._report_status("Auth response sent")
            else:
                self._report_status(f"Auth failed: {self._authenticator.last_error}")
        
        elif packet_type == PacketType.AUTH_SUCCESS:
            self._report_status("Authentication successful")
        
        elif packet_type == PacketType.AUTH_FAIL:
            self._report_status("Authentication failed")
            self._running = False
    
    def _decoder_loop(self):
        """
        Stage B: Decoder Engine Thread.
        
        Consumes video packets, decodes to YUV frames, outputs to DroppingQueue.
        """
        while self._running:
            try:
                # Get video packet (blocking with timeout)
                h264_data = self._video_queue.get(timeout=0.1)
                
                # Decode
                frame = self._video_decoder.decode(h264_data)
                
                if frame:
                    # Put to dropping queue (overwrites old frame if present)
                    dropped = self._frame_queue.put(frame)
                    if dropped:
                        pass  # Normal for real-time - old frame discarded
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[Decoder] Error: {e}")
    
    def _start_render_polling(self):
        """Start polling the frame queue for rendering."""
        # SDL window handles its own event loop and frame display
        # We just need to push frames to it
        def render_poll():
            while self._running and self._sdl_window and self._sdl_window.is_running:
                frame = self._frame_queue.get(timeout=0.016)  # ~60fps polling
                if frame and self._sdl_window:
                    self._sdl_window.update_frame(
                        frame.yuv_bytes,
                        frame.width,
                        frame.height
                    )
        
        # Run in separate thread to not block
        render_thread = threading.Thread(
            target=render_poll,
            name="Render_Poll",
            daemon=True
        )
        render_thread.start()
    
    def _on_resolution_change(self, width: int, height: int):
        """Handle video resolution change."""
        if self._sdl_window:
            self._sdl_window.set_video_size(width, height)
        self._report_status(f"Video: {width}x{height}")
    
    def _report_status(self, message: str):
        """Report status message."""
        print(f"[Pipeline] {message}")
        if self._status_callback:
            self._status_callback(message)
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def frame_queue(self) -> DroppingQueue[YUVFrame]:
        """Get the frame queue for external rendering."""
        return self._frame_queue
    
    @property
    def video_decoder(self) -> PyAVDecoder:
        """Get the video decoder for stats."""
        return self._video_decoder
