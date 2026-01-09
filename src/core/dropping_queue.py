"""
DroppingQueue - Latency-controlled queue for real-time video frames.

This queue maintains a maximum size of 1, always keeping the newest frame.
When a new frame arrives and the queue is full, the old frame is discarded.
This guarantees the renderer always displays the latest frame, eliminating
latency accumulation from buffering.
"""

import threading
from typing import Generic, Optional, TypeVar

T = TypeVar('T')


class DroppingQueue(Generic[T]):
    """
    Thread-safe queue that drops old items when full to prevent latency.
    
    Unlike a standard queue that blocks or raises when full, this queue
    overwrites the existing item with the new one. This ensures real-time
    systems always process the most recent data.
    
    Typical use:
        - maxsize=1 for video frames (always show latest)
        - maxsize=2-3 for audio (small buffer for jitter)
    """
    
    def __init__(self, maxsize: int = 1):
        """
        Initialize the dropping queue.
        
        Args:
            maxsize: Maximum number of items to hold. Default 1 (newest only).
        """
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        
        self._maxsize = maxsize
        self._items: list = []
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
    
    @property
    def maxsize(self) -> int:
        return self._maxsize
    
    def put(self, item: T) -> bool:
        """
        Put an item into the queue, dropping oldest if full.
        
        Args:
            item: The item to add.
            
        Returns:
            True if an item was dropped, False otherwise.
        """
        dropped = False
        with self._lock:
            if len(self._items) >= self._maxsize:
                # Drop oldest item(s) to make room
                self._items.pop(0)
                dropped = True
            self._items.append(item)
            self._not_empty.notify()
        return dropped
    
    def get(self, timeout: Optional[float] = None) -> Optional[T]:
        """
        Get an item from the queue.
        
        Args:
            timeout: Maximum time to wait in seconds. None for non-blocking.
            
        Returns:
            The item, or None if queue is empty (non-blocking) or timeout.
        """
        with self._not_empty:
            if not self._items:
                if timeout is None:
                    return None
                # Wait for item with timeout
                self._not_empty.wait(timeout)
                if not self._items:
                    return None
            return self._items.pop(0)
    
    def get_nowait(self) -> Optional[T]:
        """
        Get an item without waiting.
        
        Returns:
            The item, or None if queue is empty.
        """
        return self.get(timeout=None)
    
    def clear(self):
        """Clear all items from the queue."""
        with self._lock:
            self._items.clear()
    
    def qsize(self) -> int:
        """Return the current number of items in the queue."""
        with self._lock:
            return len(self._items)
    
    def empty(self) -> bool:
        """Return True if the queue is empty."""
        with self._lock:
            return len(self._items) == 0
    
    def full(self) -> bool:
        """Return True if the queue is at max capacity."""
        with self._lock:
            return len(self._items) >= self._maxsize
