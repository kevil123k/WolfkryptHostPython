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
    
    def __init__(self):
        self._codec: Optional[av.CodecContext] = None
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._initialized = False
        self.last_error = ""
        self._frame_callback: Optional[Callable[[np.ndarray], None]] = None
        self._error_count = 0
        self._max_errors_before_reset = 10
    
    def set_frame_callback(self, callback: Callable[[np.ndarray], None]):
        """Set callback for decoded frames."""
        self._frame_callback = callback
    
    def set_sps(self, sps: bytes):
        """Set Sequence Parameter Set (SPS)."""
        self._sps = sps
        self._try_initialize()
    
    def set_pps(self, pps: bytes):
        """Set Picture Parameter Set (PPS)."""
        self._pps = pps
        self._try_initialize()
    
    def _try_initialize(self):
        """Try to initialize decoder with SPS/PPS."""
        if self._sps and self._pps and not self._initialized:
            try:
                codec = av.codec.Codec('h264', 'r')
                self._codec = codec.create()
                # Don't set extradata, just decode SPS/PPS as regular NAL units
                self._initialized = True
                print("[VideoDecoder] Initialized without extradata - will decode SPS/PPS in-band")
            except Exception as e:
                self.last_error = f"Failed to initialize codec: {e}"
    
    def decode(self, nal_unit: bytes) -> Optional[np.ndarray]:
        """Decode a NAL unit and return RGB frame as numpy array."""
        if not self._initialized or not self._codec:
            return None
        
        try:
            # Create packet from NAL unit
            # If we have SPS/PPS and this is a new IDR frame, prepend them
            if self._sps and self._pps:
                # Check if this is an IDR frame (NAL type 5) - may need SPS/PPS
                nal_type = nal_unit[4] & 0x1F if len(nal_unit) > 4 else 0
                if nal_type == 5:  # IDR frame
                    # Prepend SPS and PPS to ensure decoder has them
                    combined = self._sps + self._pps + nal_unit
                    packet = av.Packet(combined)
                else:
                    packet = av.Packet(nal_unit)
            else:
                packet = av.Packet(nal_unit)
            
            # Decode frames
            for frame in self._codec.decode(packet):
                self._error_count = 0  # Reset on success
                # Convert to RGB
                rgb_frame = frame.to_ndarray(format='rgb24')
                
                if self._frame_callback:
                    self._frame_callback(rgb_frame)
                
                return rgb_frame
        except Exception as e:
            self._error_count += 1
            
            # Only log every 10th error to reduce spam
            if self._error_count <= 1 or self._error_count % 10 == 0:
                self.last_error = f"Decode error: {e}"
            
            if self._error_count >= self._max_errors_before_reset:
                print(f"[VideoDecoder] Too many errors ({self._error_count}), resetting decoder")
                self.reset()
                self._error_count = 0
            
            return None
        
        return None
    
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
        """Reset the decoder."""
        self._codec = None
        self._sps = None
        self._pps = None
        self._initialized = False
