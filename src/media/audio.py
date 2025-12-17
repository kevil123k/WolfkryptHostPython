"""
AAC Audio Decoder using PyAV (FFmpeg).
"""

from typing import Optional, Callable
import numpy as np

try:
    import av
except ImportError:
    av = None


class AudioDecoder:
    """Decodes AAC audio frames using FFmpeg via PyAV."""
    
    def __init__(self):
        self._codec: Optional[av.CodecContext] = None
        self._config: Optional[bytes] = None
        self._initialized = False
        self.last_error = ""
        self._sample_callback: Optional[Callable[[np.ndarray, int], None]] = None
        self.sample_rate = 44100
        self.channels = 2
    
    def set_sample_callback(self, callback: Callable[[np.ndarray, int], None]):
        """Set callback for decoded audio samples."""
        self._sample_callback = callback
    
    def set_config(self, config: bytes):
        """Set AAC audio specific config (2 bytes)."""
        self._config = config
        self._try_initialize()
    
    def _try_initialize(self):
        """Try to initialize decoder with config."""
        if self._config and not self._initialized:
            try:
                codec = av.codec.Codec('aac', 'r')
                self._codec = codec.create()
                self._codec.extradata = self._config
                self._initialized = True
                print("[AudioDecoder] Initialized with AAC config")
            except Exception as e:
                self.last_error = f"Failed to initialize codec: {e}"
    
    def decode(self, aac_frame: bytes) -> Optional[np.ndarray]:
        """Decode an AAC frame and return audio samples as numpy array."""
        if not self._initialized or not self._codec:
            return None
        
        try:
            # Create packet from AAC frame
            packet = av.Packet(aac_frame)
            
            # Decode frames
            for frame in self._codec.decode(packet):
                # Get audio samples as float32 numpy array
                samples = frame.to_ndarray()
                
                # Update sample rate and channels
                self.sample_rate = frame.sample_rate
                self.channels = len(frame.layout.channels)
                
                if self._sample_callback:
                    self._sample_callback(samples, self.sample_rate)
                
                return samples
        except Exception as e:
            self.last_error = f"Decode error: {e}"
            return None
        
        return None
    
    def flush(self):
        """Flush any remaining samples."""
        if self._codec:
            try:
                for frame in self._codec.decode(None):
                    if self._sample_callback:
                        samples = frame.to_ndarray()
                        self._sample_callback(samples, frame.sample_rate)
            except Exception:
                pass
    
    def reset(self):
        """Reset the decoder."""
        self._codec = None
        self._config = None
        self._initialized = False
