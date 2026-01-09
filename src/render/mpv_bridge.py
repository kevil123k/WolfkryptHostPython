"""
MPV Bridge - Low-latency video player using MPV subprocess.

This module spawns MPV as a subprocess with scrcpy-like configuration
for real-time H.264 streaming with hardware acceleration.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional


class MPVBridge:
    """
    MPV subprocess manager for low-latency video playback.
    
    Uses MPV's built-in hardware acceleration and zero-latency profile
    to achieve <50ms display latency.
    """
    
    # MPV configuration for real-time streaming (scrcpy-like)
    MPV_LOW_LATENCY_FLAGS = [
        # Input format
        '--demuxer-lavf-format=h264',  # Raw H.264 Annex B input
        '--demuxer-lavf-analyzeduration=0',  # No analysis delay
        '--demuxer-lavf-probesize=32',  # Minimal probing
        
        # Low latency profile
        '--profile=low-latency',  # Disables internal buffering
        '--untimed',  # Render immediately, ignore timestamps
        '--no-cache',  # No caching
        '--cache-pause=no',  # Never pause for buffering
        
        # Hardware acceleration
        '--hwdec=auto',  # Auto-select (d3d11va on Windows)
        
        # Video output
        '--vo=gpu',  # GPU-accelerated output
        '--gpu-api=d3d11',  # Direct3D 11 on Windows
        '--video-sync=audio',  # Sync to audio when available
        
        # Window settings
        '--force-window=immediate',  # Show window immediately
        '--keepaspect',  # Maintain aspect ratio
        '--autofit=50%',  # Window size
        '--title=Wolfkrypt Mirror',
        
        # Performance
        '--vd-lavc-threads=1',  # Single decode thread (lowest latency)
        '--demuxer-thread=no',  # No demuxer thread
        
        # Disable unused features
        '--no-osc',  # No on-screen controller
        '--no-osd-bar',  # No progress bar
        '--no-input-default-bindings',  # Minimal input handling
        
        # Read from stdin
        '-',
    ]
    
    def __init__(self, mpv_path: Optional[str] = None):
        """
        Initialize the MPV bridge.
        
        Args:
            mpv_path: Path to mpv executable. If None, searches PATH.
        """
        self._mpv_path = mpv_path or self._find_mpv()
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._stderr_thread: Optional[threading.Thread] = None
        
    def _find_mpv(self) -> Optional[str]:
        """Find MPV executable."""
        # Check common locations on Windows
        common_paths = [
            # User's test path from logs
            r"C:\Users\Gurukrupa Sound\Desktop\Test\WolfkryptHostPython\mpv\mpv.exe",
            # Relative to project
            "mpv/mpv.exe",
            "./mpv/mpv.exe",
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                return path
        
        # Check PATH
        mpv_in_path = shutil.which('mpv')
        if mpv_in_path:
            return mpv_in_path
            
        return None
    
    def start(self) -> bool:
        """Start the MPV subprocess."""
        if self._running:
            return True
            
        if not self._mpv_path:
            print("[MPVBridge] ERROR: mpv.exe not found")
            print("[MPVBridge] Please install MPV or set mpv_path")
            return False
        
        print(f"[MPVBridge] Starting: {self._mpv_path}")
        
        try:
            cmd = [self._mpv_path] + self.MPV_LOW_LATENCY_FLAGS
            
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,  # Unbuffered
            )
            
            self._running = True
            print(f"[MPVBridge] Started (PID: {self._process.pid})")
            
            # Start stderr reader for diagnostics
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                daemon=True
            )
            self._stderr_thread.start()
            
            return True
            
        except Exception as e:
            print(f"[MPVBridge] Failed to start: {e}")
            return False
    
    def write(self, data: bytes) -> bool:
        """
        Write H.264 data to MPV stdin.
        
        Args:
            data: Raw H.264 NAL units (Annex B format with start codes)
            
        Returns:
            True if successful, False on error.
        """
        if not self._running or not self._process or not self._process.stdin:
            return False
        
        try:
            self._process.stdin.write(data)
            # Don't flush every write - let OS buffer handle it
            return True
        except (BrokenPipeError, OSError) as e:
            print(f"[MPVBridge] Write error: {e}")
            self._running = False
            return False
    
    def flush(self):
        """Flush the stdin buffer."""
        if self._process and self._process.stdin:
            try:
                self._process.stdin.flush()
            except Exception:
                pass
    
    def _read_stderr(self):
        """Read MPV stderr for diagnostics."""
        while self._running and self._process:
            try:
                line = self._process.stderr.readline()
                if line:
                    msg = line.decode('utf-8', errors='ignore').strip()
                    if msg:
                        # Filter out noisy messages
                        if 'error' in msg.lower() or 'warn' in msg.lower():
                            print(f"[MPV] {msg}")
                else:
                    if self._process.poll() is not None:
                        print(f"[MPVBridge] Process exited: {self._process.poll()}")
                        break
            except Exception:
                break
    
    def stop(self):
        """Stop the MPV subprocess."""
        self._running = False
        
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        
        print("[MPVBridge] Stopped")
    
    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None
