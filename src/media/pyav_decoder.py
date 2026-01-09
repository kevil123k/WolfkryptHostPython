"""
PyAV-based H.264 Video Decoder with Hardware Acceleration.

This module provides in-process H.264 decoding using PyAV (FFmpeg wrapper).
It replaces the FFmpeg subprocess approach for lower latency and better
integration with the SDL2 rendering pipeline.
"""

import platform
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

try:
    import av
    from av.video.frame import VideoFrame
    PYAV_AVAILABLE = True
except ImportError:
    PYAV_AVAILABLE = False
    print("[PyAVDecoder] Warning: PyAV not available")


@dataclass
class YUVFrame:
    """Container for decoded YUV420P frame data."""
    y_plane: bytes
    u_plane: bytes
    v_plane: bytes
    width: int
    height: int
    
    @property
    def yuv_bytes(self) -> bytes:
        """Return concatenated YUV420P bytes for SDL2 texture upload."""
        return self.y_plane + self.u_plane + self.v_plane
    
    @property
    def size(self) -> Tuple[int, int]:
        return (self.width, self.height)


class PyAVDecoder:
    """
    Hardware-accelerated H.264 decoder using PyAV.
    
    Features:
    - Windows: d3d11va (Direct3D 11), dxva2 fallback
    - Linux: vaapi, software fallback
    - macOS: videotoolbox, software fallback
    - Outputs YUV420P frames for direct SDL2 texture upload
    """
    
    def __init__(self, hw_accel: Optional[str] = None):
        """
        Initialize the decoder.
        
        Args:
            hw_accel: Hardware acceleration method. None for auto-detect.
                      Options: 'd3d11va', 'dxva2', 'vaapi', 'videotoolbox', 'auto', None
        """
        if not PYAV_AVAILABLE:
            raise RuntimeError("PyAV is not installed")
        
        self._hw_accel = hw_accel or self._detect_hw_accel()
        self._codec_ctx: Optional[av.codec.CodecContext] = None
        self._running = False
        self._lock = threading.Lock()
        
        # SPS/PPS storage
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._config_ready = False
        
        # Callbacks
        self._frame_callback: Optional[Callable[[YUVFrame], None]] = None
        self._resolution_callback: Optional[Callable[[int, int], None]] = None
        
        # Stats
        self._frames_decoded = 0
        self._width = 0
        self._height = 0
        
    def _detect_hw_accel(self) -> str:
        """Detect best available hardware acceleration for this platform."""
        system = platform.system()
        
        if system == 'Windows':
            # Try d3d11va first (better than dxva2)
            return 'd3d11va'
        elif system == 'Linux':
            return 'vaapi'
        elif system == 'Darwin':  # macOS
            return 'videotoolbox'
        else:
            return 'auto'
    
    def set_frame_callback(self, callback: Callable[[YUVFrame], None]):
        """Set callback for decoded frames."""
        self._frame_callback = callback
    
    def set_resolution_callback(self, callback: Callable[[int, int], None]):
        """Set callback for when resolution is detected from SPS."""
        self._resolution_callback = callback
    
    def set_sps(self, sps: bytes):
        """
        Set Sequence Parameter Set.
        
        The SPS contains codec configuration including resolution.
        Start code (00 00 00 01) is added if missing.
        """
        # Ensure start code
        if not sps.startswith(b'\x00\x00\x00\x01') and not sps.startswith(b'\x00\x00\x01'):
            sps = b'\x00\x00\x00\x01' + sps
        
        self._sps = sps
        print(f"[PyAVDecoder] SPS: {len(sps)} bytes")
        
        # Try to initialize decoder if we have both SPS and PPS
        if self._pps and not self._running:
            self._initialize_decoder()
    
    def set_pps(self, pps: bytes):
        """
        Set Picture Parameter Set.
        
        Start code (00 00 00 01) is added if missing.
        """
        if not pps.startswith(b'\x00\x00\x00\x01') and not pps.startswith(b'\x00\x00\x01'):
            pps = b'\x00\x00\x00\x01' + pps
        
        self._pps = pps
        print(f"[PyAVDecoder] PPS: {len(pps)} bytes")
        
        # Try to initialize decoder if we have both SPS and PPS
        if self._sps and not self._running:
            self._initialize_decoder()
    
    def _initialize_decoder(self) -> bool:
        """Initialize the H.264 codec context with hardware acceleration."""
        if self._running:
            return True
        
        if not self._sps or not self._pps:
            print("[PyAVDecoder] Cannot initialize: missing SPS or PPS")
            return False
        
        try:
            # Find H.264 decoder
            codec = av.Codec('h264', 'r')  # 'r' for decoder
            
            # Create codec context with hardware acceleration options
            self._codec_ctx = av.CodecContext.create(codec)
            
            # Configure for low latency
            self._codec_ctx.thread_type = 'FRAME'  # Frame-level threading
            self._codec_ctx.thread_count = 1  # Single thread for lowest latency
            
            # Set hardware acceleration (if supported)
            try:
                if self._hw_accel and self._hw_accel != 'auto':
                    self._codec_ctx.options = {'hwaccel': self._hw_accel}
                    print(f"[PyAVDecoder] Hardware acceleration: {self._hw_accel}")
            except Exception as e:
                print(f"[PyAVDecoder] HW accel failed, using software: {e}")
            
            # Open codec
            self._codec_ctx.open()
            
            # Send SPS/PPS as extradata to initialize decoder
            config_data = self._sps + self._pps
            
            # Create a packet with config data
            packet = av.Packet(config_data)
            
            # Decode to trigger initialization
            try:
                for frame in self._codec_ctx.decode(packet):
                    # Unlikely to get frames from just SPS/PPS, but handle if we do
                    self._process_frame(frame)
            except av.AVError:
                pass  # Normal - SPS/PPS don't produce frames
            
            self._running = True
            self._config_ready = True
            print(f"[PyAVDecoder] Initialized with {self._hw_accel} acceleration")
            return True
            
        except Exception as e:
            print(f"[PyAVDecoder] Initialization failed: {e}")
            # Try without hardware acceleration
            return self._initialize_software_decoder()
    
    def _initialize_software_decoder(self) -> bool:
        """Fallback to software decoding."""
        try:
            codec = av.Codec('h264', 'r')
            self._codec_ctx = av.CodecContext.create(codec)
            self._codec_ctx.thread_type = 'FRAME'
            self._codec_ctx.thread_count = 2  # Allow 2 threads for software
            self._codec_ctx.open()
            
            # Send config
            if self._sps and self._pps:
                config_data = self._sps + self._pps
                packet = av.Packet(config_data)
                try:
                    for frame in self._codec_ctx.decode(packet):
                        self._process_frame(frame)
                except av.AVError:
                    pass
            
            self._running = True
            self._config_ready = True
            print("[PyAVDecoder] Initialized with software decoding")
            return True
            
        except Exception as e:
            print(f"[PyAVDecoder] Software decoder init failed: {e}")
            return False
    
    def decode(self, h264_data: bytes) -> Optional[YUVFrame]:
        """
        Decode H.264 NAL unit(s) and return YUV frame if available.
        
        Args:
            h264_data: Raw H.264 data (Annex B format with start codes)
            
        Returns:
            YUVFrame if a frame was decoded, None otherwise.
        """
        if not self._running or not self._codec_ctx:
            # Try to initialize if we have config
            if self._sps and self._pps:
                if not self._initialize_decoder():
                    return None
            else:
                return None
        
        # Ensure start code
        if not h264_data.startswith(b'\x00\x00\x00\x01') and not h264_data.startswith(b'\x00\x00\x01'):
            h264_data = b'\x00\x00\x00\x01' + h264_data
        
        try:
            packet = av.Packet(h264_data)
            
            for frame in self._codec_ctx.decode(packet):
                return self._process_frame(frame)
                
        except av.AVError as e:
            # Decoder errors are often recoverable (corrupt frame, etc.)
            if self._frames_decoded == 0:
                print(f"[PyAVDecoder] Decode error: {e}")
            return None
        except Exception as e:
            print(f"[PyAVDecoder] Unexpected error: {e}")
            return None
        
        return None
    
    def _process_frame(self, frame: 'VideoFrame') -> YUVFrame:
        """Convert PyAV VideoFrame to YUVFrame for SDL2."""
        self._frames_decoded += 1
        
        # Update resolution if changed
        if frame.width != self._width or frame.height != self._height:
            self._width = frame.width
            self._height = frame.height
            print(f"[PyAVDecoder] Resolution: {self._width}x{self._height}")
            
            if self._resolution_callback:
                self._resolution_callback(self._width, self._height)
        
        # Convert to YUV420P if needed
        if frame.format.name != 'yuv420p':
            frame = frame.reformat(format='yuv420p')
        
        # Extract plane data
        y_plane = bytes(frame.planes[0])
        u_plane = bytes(frame.planes[1])
        v_plane = bytes(frame.planes[2])
        
        yuv_frame = YUVFrame(
            y_plane=y_plane,
            u_plane=u_plane,
            v_plane=v_plane,
            width=frame.width,
            height=frame.height
        )
        
        # Invoke callback
        if self._frame_callback:
            self._frame_callback(yuv_frame)
        
        # Periodic status
        if self._frames_decoded == 1:
            print(f"[PyAVDecoder] First frame: {frame.width}x{frame.height}")
        elif self._frames_decoded % 300 == 0:
            print(f"[PyAVDecoder] Decoded {self._frames_decoded} frames")
        
        return yuv_frame
    
    def stop(self):
        """Stop the decoder and release resources."""
        self._running = False
        
        with self._lock:
            if self._codec_ctx:
                try:
                    self._codec_ctx.close()
                except Exception:
                    pass
                self._codec_ctx = None
        
        print(f"[PyAVDecoder] Stopped ({self._frames_decoded} frames decoded)")
    
    def reset(self):
        """Reset the decoder state for a new stream."""
        self.stop()
        self._sps = None
        self._pps = None
        self._config_ready = False
        self._frames_decoded = 0
        self._width = 0
        self._height = 0
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded
    
    @property
    def resolution(self) -> Tuple[int, int]:
        return (self._width, self._height)
