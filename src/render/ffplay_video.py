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
                # Don't flush buffer immediately - let frames flow naturally
                # This prevents overwhelming FFplay during initialization
                print(f"[FFplay] {len(self._frame_buffer)} frames buffered, will send gradually")
    
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
        
        # Simplified FFplay command optimized for reliability and low latency
        cmd = [
            ffplay_path,
            
            # Input format - specify before input
            '-f', 'h264',
            
            # Low-latency flags
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-framedrop',
            
            # Give FFplay more data to work with for initialization
            '-probesize', '32768',
            '-analyzeduration', '500000',  # 0.5 seconds
            
            # Input from pipe
            '-i', 'pipe:0',
            
            # Sync and threading
            '-sync', 'video',
            
            # No audio
            '-an',
            
            # Window options
            '-window_title', self._title,
            '-alwaysontop',
            
            # Video filter to ensure proper scaling
            '-vf', 'scale=iw:ih',
            
            '-loglevel', 'info'
        ]
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0
            )
            print(f"[FFplay] Started (PID: {self._process.pid})")
            
        except Exception as e:
            print(f"[FFplay] Failed to start: {e}")
            self._running = False
            return False
            
        # Start stderr reader
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        
        # Give FFplay more time to initialize before sending data
        import time
        time.sleep(0.1)  # 100ms delay for FFplay to set up pipes and renderer
        
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
                # Send SPS and PPS as separate NAL units
                self._process.stdin.write(self._sps)
                self._process.stdin.write(self._pps)
                # Flush immediately to ensure FFplay gets config
                self._process.stdin.flush()
                self._config_sent = True
                print("[FFplay] Sent SPS/PPS config")
                # Reset frame count after config
                self._frame_count = 0
                
                # Wait longer for FFplay to fully initialize the filtergraph
                import time
                time.sleep(0.15)  # 150ms for FFplay to set up video pipeline
                self._ready = True
                print("[FFplay] Ready for video frames")
                
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
        
        # Send buffered frames first (one per call to avoid overwhelming FFplay)
        with self._lock:
            if self._frame_buffer:
                buffered_frame = self._frame_buffer.pop(0)
                try:
                    if self._process and self._process.stdin:
                        self._process.stdin.write(buffered_frame)
                except Exception as e:
                    print(f"[FFplay] Buffer send error: {e}")
                    self._running = False
                    self._ready = False
                    return
                
        if not self._process or not self._process.stdin:
            return
            
        # Send current H.264 data
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
