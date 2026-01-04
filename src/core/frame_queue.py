from queue import Queue, Full
from typing import Optional
import logging

class FrameQueue:
    """Bounded queue with frame dropping on overflow."""
    
    def __init__(self, maxsize: int = 30):
        self._queue = Queue(maxsize=maxsize)
        self._dropped_frames = 0
        self._total_frames = 0
    
    def put(self, frame: bytes) -> bool:
        """Put frame in queue, drop if full."""
        self._total_frames += 1
        try:
            self._queue.put_nowait(frame)
            return True
        except Full:
            self._dropped_frames += 1
            if self._dropped_frames % 10 == 0:
                logging.warning(
                    f"Frame queue full - dropped {self._dropped_frames}/{self._total_frames} frames "
                    f"({100*self._dropped_frames/self._total_frames:.1f}%)"
                )
            return False
    
    def get(self, timeout: float = 0.5) -> Optional[bytes]:
        """Get frame from queue."""
        try:
            return self._queue.get(timeout=timeout)
        except:
            return None
    
    @property
    def drop_rate(self) -> float:
        """Get frame drop rate."""
        if self._total_frames == 0:
            return 0.0
        return self._dropped_frames / self._total_frames