"""
H.264 Video Decoder using PyAV (FFmpeg).
"""

from typing import Optional, Callable
import numpy as np

try:
    import av
except ImportError:
    av = None


class VideoDecoder:
    """Decodes H.264 video frames using FFmpeg via PyAV."""
    
    # NAL unit start codes
    NAL_START_CODE_3 = bytes([0x00, 0x00, 0x01])
    NAL_START_CODE_4 = bytes([0x00, 0x00, 0x00, 0x01])
    
    def __init__(self):
        self._codec: Optional[av.CodecContext] = None
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._initialized = False
        self.last_error = ""
        self._frame_callback: Optional[Callable[[np.ndarray], None]] = None
        self._error_count = 0
        self._max_errors_before_reset = 10
        self._frames_decoded = 0
    
    def set_frame_callback(self, callback: Callable[[np.ndarray], None]):
        """Set callback for decoded frames."""
        self._frame_callback = callback
    
    def set_sps(self, sps: bytes):
        """Set Sequence Parameter Set (SPS)."""
        # Ensure SPS has a start code
        if not sps.startswith(self.NAL_START_CODE_3) and not sps.startswith(self.NAL_START_CODE_4):
            sps = self.NAL_START_CODE_4 + sps
        self._sps = sps
        print(f"[VideoDecoder] SPS received: {len(sps)} bytes")
        self._try_initialize()
    
    def set_pps(self, pps: bytes):
        """Set Picture Parameter Set (PPS)."""
        # Ensure PPS has a start code
        if not pps.startswith(self.NAL_START_CODE_3) and not pps.startswith(self.NAL_START_CODE_4):
            pps = self.NAL_START_CODE_4 + pps
        self._pps = pps
        print(f"[VideoDecoder] PPS received: {len(pps)} bytes")
        self._try_initialize()
    
    def _try_initialize(self):
        """Try to initialize decoder with SPS/PPS."""
        if self._sps and self._pps and not self._initialized:
            try:
                codec = av.codec.Codec('h264', 'r')
                self._codec = codec.create()
                self._initialized = True
                print("[VideoDecoder] Initialized - ready to decode frames")
                
                # Decode SPS and PPS first to configure the decoder
                try:
                    combined = self._sps + self._pps
                    packet = av.Packet(combined)
                    # This typically won't produce frames, just configures decoder
                    for _ in self._codec.decode(packet):
                        pass
                    print("[VideoDecoder] SPS/PPS decoded successfully")
                except Exception as e:
                    print(f"[VideoDecoder] SPS/PPS decode note: {e}")
                    
            except Exception as e:
                self.last_error = f"Failed to initialize codec: {e}"
                print(f"[VideoDecoder] {self.last_error}")
    
    def _get_nal_type(self, nal_unit: bytes) -> int:
        """Extract NAL type from a NAL unit."""
        if len(nal_unit) < 5:
            return 0
        
        # Find the NAL header byte after start code
        if nal_unit.startswith(self.NAL_START_CODE_4):
            nal_header_offset = 4
        elif nal_unit.startswith(self.NAL_START_CODE_3):
            nal_header_offset = 3
        else:
            # No start code, first byte is NAL header
            nal_header_offset = 0
        
        if len(nal_unit) <= nal_header_offset:
            return 0
            
        return nal_unit[nal_header_offset] & 0x1F
    
    def decode(self, nal_unit: bytes) -> Optional[np.ndarray]:
        """Decode a NAL unit and return RGB frame as numpy array."""
        if not self._initialized or not self._codec:
            return None
        
        if len(nal_unit) == 0:
            return None
        
        try:
            nal_type = self._get_nal_type(nal_unit)
            
            # For IDR frames (type 5), prepend SPS/PPS
            if nal_type == 5 and self._sps and self._pps:
                combined = self._sps + self._pps + nal_unit
                packet = av.Packet(combined)
            else:
                packet = av.Packet(nal_unit)
            
            # Decode frames
            for frame in self._codec.decode(packet):
                self._error_count = 0  # Reset on success
                self._frames_decoded += 1
                
                # Convert to RGB
                rgb_frame = frame.to_ndarray(format='rgb24')
                
                if self._frames_decoded == 1:
                    print(f"[VideoDecoder] First frame decoded: {rgb_frame.shape}")
                
                if self._frame_callback:
                    self._frame_callback(rgb_frame)
                
                return rgb_frame
                
        except Exception as e:
            self._error_count += 1
            
            # Log first error and every 10th
            if self._error_count == 1 or self._error_count % 10 == 0:
                self.last_error = f"Decode error (nal_type={self._get_nal_type(nal_unit)}): {e}"
                print(f"[VideoDecoder] {self.last_error}")
            
            if self._error_count >= self._max_errors_before_reset:
                print(f"[VideoDecoder] Too many errors ({self._error_count}), resetting decoder")
                self._reinitialize()
                self._error_count = 0
            
            return None
        
        return None
    
    def _reinitialize(self):
        """Reinitialize codec while preserving SPS/PPS."""
        # Save SPS/PPS
        saved_sps = self._sps
        saved_pps = self._pps
        
        # Reset codec
        self._codec = None
        self._initialized = False
        
        # Restore SPS/PPS and reinitialize
        self._sps = saved_sps
        self._pps = saved_pps
        self._try_initialize()
    
    def flush(self):
        """Flush any remaining frames."""
        if self._codec:
            try:
                for frame in self._codec.decode(None):
                    if self._frame_callback:
                        rgb_frame = frame.to_ndarray(format='rgb24')
                        self._frame_callback(rgb_frame)
            except Exception:
                pass
    
    def reset(self):
        """Full reset of the decoder (clears SPS/PPS)."""
        self._codec = None
        self._sps = None
        self._pps = None
        self._initialized = False
        self._frames_decoded = 0
