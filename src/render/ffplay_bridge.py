"""
FFplay Bridge - Low-latency video player using FFplay subprocess.
Uses ffplay from C:\\ffmpeg for simpler, more reliable playback.
"""

import os
import shutil
import subprocess
import threading
from typing import Optional


class FFplayBridge:
    """FFplay subprocess for low-latency H.264 playback."""
    
    FFPLAY_FLAGS = [
        # Input
        '-f', 'h264',
        '-flags', 'low_delay',
        '-fflags', 'nobuffer',
        '-probesize', '32',
        '-analyzeduration', '0',
        
        # Use DXVA2 (DirectX 9, works on Intel HD 4000)
        # FFplay's Vulkan renderer is broken, so use dxva2 output
        '-hwaccel', 'dxva2',
        '-hwaccel_output_format', 'dxva2_vld',
        
        # Display
        '-vf', 'hwdownload,format=nv12,format=yuv420p',  # Convert from DXVA to SDL
        '-sync', 'ext',
        '-framedrop',
        '-fast',
        '-infbuf',
        
        # Window
        '-window_title', 'Wolfkrypt Mirror',
        '-x', '480',
        '-y', '640',
        
        # No audio
        '-an',
        
        # Input from stdin
        '-i', 'pipe:0',
    ]
    
    def __init__(self, ffplay_path: Optional[str] = None):
        self._ffplay_path = ffplay_path or self._find_ffplay()
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._stderr_thread: Optional[threading.Thread] = None
        
    def _find_ffplay(self) -> Optional[str]:
        paths = [
            r"C:\ffmpeg\bin\ffplay.exe",
            "ffplay.exe",
        ]
        
        for path in paths:
            if os.path.exists(path):
                return path
        
        return shutil.which('ffplay')
    
    def start(self) -> bool:
        if self._running:
            return True
            
        if not self._ffplay_path:
            print("[FFplayBridge] ERROR: ffplay.exe not found")
            return False
        
        print(f"[FFplayBridge] Starting: {self._ffplay_path}")
        
        try:
            cmd = [self._ffplay_path] + self.FFPLAY_FLAGS
            
            # Don't use CREATE_NO_WINDOW - ffplay needs a window!
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            
            self._running = True
            print(f"[FFplayBridge] Started (PID: {self._process.pid})")
            
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                daemon=True
            )
            self._stderr_thread.start()
            
            return True
            
        except Exception as e:
            print(f"[FFplayBridge] Failed: {e}")
            return False
    
    def write(self, data: bytes) -> bool:
        if not self._running or not self._process or not self._process.stdin:
            return False
        
        try:
            self._process.stdin.write(data)
            return True
        except (BrokenPipeError, OSError) as e:
            print(f"[FFplayBridge] Write error: {e}")
            self._running = False
            return False
    
    def flush(self):
        if self._process and self._process.stdin:
            try:
                self._process.stdin.flush()
            except Exception:
                pass
    
    def _read_stderr(self):
        while self._running and self._process:
            try:
                line = self._process.stderr.readline()
                if line:
                    msg = line.decode('utf-8', errors='ignore').strip()
                    if msg:
                        print(f"[FFplay] {msg}")
                else:
                    if self._process.poll() is not None:
                        print(f"[FFplayBridge] Exited: {self._process.poll()}")
                        break
            except Exception:
                break
    
    def stop(self):
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
        
        print("[FFplayBridge] Stopped")
    
    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None
