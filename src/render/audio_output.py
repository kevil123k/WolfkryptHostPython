"""
Audio output using sounddevice (PortAudio).
"""

from typing import Optional
import queue
import threading
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None


class AudioPlayer:
    """Low-latency audio playback using sounddevice."""
    
    def __init__(self, sample_rate: int = 44100, channels: int = 2):
        self._sample_rate = sample_rate
        self._channels = channels
        self._stream: Optional[sd.OutputStream] = None
        self._queue: queue.Queue = queue.Queue(maxsize=10)
        self._running = False
        self._thread: Optional[threading.Thread] = None
    
    def start(self):
        """Start the audio output stream."""
        if sd is None:
            print("[AudioPlayer] sounddevice not available")
            return False
        
        try:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype='float32',
                callback=self._audio_callback,
                blocksize=1024,
                latency='low'
            )
            self._stream.start()
            self._running = True
            print(f"[AudioPlayer] Started: {self._sample_rate}Hz, {self._channels}ch")
            return True
        except Exception as e:
            print(f"[AudioPlayer] Failed to start: {e}")
            return False
    
    def stop(self):
        """Stop the audio output stream."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        
        # Clear queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
    
    def play(self, samples: np.ndarray):
        """Queue audio samples for playback."""
        if not self._running:
            return
        
        try:
            self._queue.put_nowait(samples)
        except queue.Full:
            # Drop oldest samples if queue is full
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(samples)
            except (queue.Empty, queue.Full):
                pass
    
    def _audio_callback(self, outdata: np.ndarray, frames: int, 
                        time_info, status):
        """Callback for sounddevice stream."""
        try:
            samples = self._queue.get_nowait()
            
            # Ensure correct shape
            if samples.ndim == 1:
                samples = samples.reshape(-1, 1)
            
            # Fill output buffer
            if len(samples) >= frames:
                outdata[:] = samples[:frames]
            else:
                outdata[:len(samples)] = samples
                outdata[len(samples):] = 0
        except queue.Empty:
            # No data, output silence
            outdata.fill(0)
    
    def set_sample_rate(self, sample_rate: int):
        """Update sample rate (requires restart)."""
        if sample_rate != self._sample_rate:
            self._sample_rate = sample_rate
            if self._running:
                self.stop()
                self.start()
    
    def set_channels(self, channels: int):
        """Update channel count (requires restart)."""
        if channels != self._channels:
            self._channels = channels
            if self._running:
                self.stop()
                self.start()
