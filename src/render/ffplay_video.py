"""
FFplay Video Player for low-latency H.264 playback.

Uses FFplay subprocess for hardware-accelerated decode and display.
This replaces the FFmpeg+SDL pipeline with a single process that handles everything.
"""

import subprocess
import shutil
import threading
from typing import Optional


class FFplayVideo:
    """
    FFplay-based video player for low-latency H.264 playback.
    
    FFplay handles both decode and display with hardware acceleration,
    eliminating the need for Python to process video frames.
    """
    
    def __init__(self, title: str = "Wolfkrypt Mirror"):
        self._title = title
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._ready = False  # True when FFplay has received SPS/PPS and is ready
        self._width = 0
        self._height = 0
        
        # SPS/PPS for decoder initialization
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._config_sent = False
        
        self._writer_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        
        # Frame counter for periodic flush (every 5 frames to balance latency vs throughput)
        self._frame_count = 0
        self._flush_interval = 5
        
        # Buffer for frames received before FFplay is ready (max 10 frames)
        self._frame_buffer = []
        self._max_buffer_size = 10
        self._lock = threading.Lock()
        
    def set_sps(self, sps: bytes):
        """Set Sequence Parameter Set."""
        if not sps.startswith(b'\x00\x00\x00\x01') and not sps.startswith(b'\x00\x00\x01'):
            sps = b'\x00\x00\x00\x01' + sps
        self._sps = sps
        print(f"[FFplay] SPS: {len(sps)} bytes")
        
        # Start if we have both SPS and PPS
        if self._pps and not self._running:
            self.start()
            
    def set_pps(self, pps: bytes):
        """Set Picture Parameter Set."""
        if not pps.startswith(b'\x00\x00\x00\x01') and not pps.startswith(b'\x00\x00\x01'):
            pps = b'\x00\x00\x00\x01' + pps
        self._pps = pps
        print(f"[FFplay] PPS: {len(pps)} bytes")
        
        # Start FFplay now that we have both SPS and PPS
        if self._sps and not self._running:
            if self.start():
                # FFplay started successfully, now send buffered frames
                self._flush_buffer()
    
    def start(self) -> bool:
        """Start FFplay subprocess."""
        if self._running:
            return True
            
        # Check if FFplay is available
        ffplay_path = shutil.which('ffplay')
        if not ffplay_path:
            print("[FFplay] ERROR: FFplay not found in PATH")
            return False
            
        self._running = True
        
        # FFplay command with low-latency flags
        cmd = [
            ffplay_path,
            '-hide_banner',
            
            # Low-latency input flags
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-probesize', '32',
            '-analyzeduration', '0',
            
            # Input format
            '-f', 'h264',
            '-i', 'pipe:0',
            
            # Display options
            '-window_title', self._title,
            '-autoexit',
            
            # Frame dropping for real-time
            '-framedrop',
            '-sync', 'video',
            
            # Disable audio (we handle it separately)
            '-an',
            
            # Keyboard shortcuts disabled
            '-nodisp', '-loglevel', 'warning'
        ]
        
        # Optimized FFplay command for lowest latency
        cmd = [
            ffplay_path,
            '-hide_banner',
            
            # Low-latency input flags
            '-fflags', 'nobuffer+fastseek+flush_packets',
            '-flags', 'low_delay',
            '-strict', 'experimental',
            '-probesize', '32',
            '-analyzeduration', '0',
            
            # Hardware acceleration (try multiple backends)
            '-hwaccel', 'auto',
            
            # Input format
            '-f', 'h264',
            '-i', 'pipe:0',
            
            # Display options
            '-window_title', self._title,
            '-autoexit',
            
            # Frame dropping and sync for real-time streaming
            '-framedrop',
            '-infbuf',  # Infinite buffer to avoid blocking (but fflags nobuffer keeps it small)
            '-sync', 'ext',  # External sync for lowest latency
            
            # Video thread count (1 for lowest latency)
            '-threads', '1',
            
            # Disable audio
            '-an',
            
            '-loglevel', 'warning'
        ]
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0
            )
            print(f"[FFplay] Started")
            
        except Exception as e:
            print(f"[FFplay] Failed to start: {e}")
            self._running = False
            return False
            
        # Start stderr reader
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        
        # Send SPS/PPS if we have them
        if self._sps and self._pps:
            self._send_config()
            
        return True
        
    def _send_config(self):
        """Send SPS/PPS to FFplay."""
        if self._config_sent or not self._sps or not self._pps:
            return
            
        try:
            if self._process and self._process.stdin:
                self._process.stdin.write(self._sps)
                self._process.stdin.write(self._pps)
                # Always flush config frames immediately
                self._process.stdin.flush()
                self._config_sent = True
                self._ready = True  # FFplay is now ready to receive frames
                print("[FFplay] Sent SPS/PPS - Ready for frames")
                # Reset frame count after config
                self._frame_count = 0
        except Exception as e:
            print(f"[FFplay] Config send error: {e}")
            self._running = False
            
    def decode(self, h264_data: bytes):
        """Send H.264 data to FFplay for decoding and display."""
        # Buffer frames if FFplay not ready yet
        if not self._ready:
            with self._lock:
                # Only buffer if we have SPS/PPS (otherwise frames are useless)
                if self._sps and self._pps:
                    # Keep buffer small to avoid latency
                    if len(self._frame_buffer) < self._max_buffer_size:
                        self._frame_buffer.append(h264_data)
                    else:
                        # Drop oldest frame to make room
                        self._frame_buffer.pop(0)
                        self._frame_buffer.append(h264_data)
            return
                
        if not self._process or not self._process.stdin:
            return
            
        # Send H.264 data directly
        try:
            self._process.stdin.write(h264_data)
            
            # Periodic flush to reduce latency while maintaining throughput
            # Flush every N frames (balance between latency and CPU overhead)
            self._frame_count += 1
            if self._frame_count >= self._flush_interval:
                self._process.stdin.flush()
                self._frame_count = 0
        except (BrokenPipeError, OSError) as e:
            print(f"[FFplay] Pipe error: {e}")
            self._running = False
            self._ready = False
            
    def _flush_buffer(self):
        """Send buffered frames to FFplay after it's ready."""
        with self._lock:
            if not self._frame_buffer:
                return
            
            print(f"[FFplay] Flushing {len(self._frame_buffer)} buffered frames")
            for frame_data in self._frame_buffer:
                try:
                    if self._process and self._process.stdin:
                        self._process.stdin.write(frame_data)
                except Exception as e:
                    print(f"[FFplay] Buffer flush error: {e}")
                    break
            
            # Clear buffer and flush pipe
            self._frame_buffer.clear()
            try:
                if self._process and self._process.stdin:
                    self._process.stdin.flush()
            except Exception:
                pass
    
    def _read_stderr(self):
        """Read FFplay stderr for diagnostics."""
        while self._running:
            try:
                if not self._process or not self._process.stderr:
                    break
                line = self._process.stderr.readline()
                if line:
                    msg = line.decode('utf-8', errors='ignore').strip()
                    if msg:
                        print(f"[FFplay] {msg}")
            except Exception:
                break
                
    def stop(self):
        """Stop FFplay."""
        self._running = False
        self._ready = False
        
        with self._lock:
            self._frame_buffer.clear()
        
        if self._process:
            try:
                self._process.stdin.close()
            except:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except:
                try:
                    self._process.kill()
                except:
                    pass
            self._process = None
            
        print("[FFplay] Stopped")
        
    # Compatibility methods for main_window.py
    def set_frame_callback(self, callback):
        """No-op for compatibility - FFplay handles display."""
        pass
        
    def set_resolution_callback(self, callback):
        """No-op for compatibility - FFplay auto-detects resolution."""
        pass
