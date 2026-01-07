"""
mpv Video Player for low-latency H.264 playback.

Uses mpv subprocess for hardware-accelerated decode and display.
mpv has superior hardware acceleration support compared to FFplay.
"""

import subprocess
import shutil
import threading
from typing import Optional


class FFplayVideo:
    """
    mpv-based video player for low-latency H.264 playback.
    
    mpv handles both decode and display with superior hardware acceleration,
    eliminating the need for Python to process video frames.
    Works better than FFplay for DirectX/Windows environments.
    """
    
    def __init__(self, title: str = "Wolfkrypt Mirror"):
        self._title = title
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._ready = False  # True when mpv has received SPS/PPS and is ready
        self._width = 0
        self._height = 0
        
        # SPS/PPS for decoder initialization
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._config_sent = False
        
        self._writer_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        
        # Frame counter for periodic flush (every 3 frames with hardware decode)
        self._frame_count = 0
        self._flush_interval = 3  # Lower interval with hardware decode
        
        # Buffer for frames received before mpv is ready (max 20 frames with hardware decode)
        self._frame_buffer = []
        self._max_buffer_size = 20  # Larger buffer for USB jitter absorption
        self._lock = threading.Lock()
        
    def _detect_hardware_accel(self) -> str:
        """Detect available hardware acceleration."""
        import platform
        
        if platform.system() != 'Windows':
            return 'auto'
        
        # Check for NVIDIA GPU
        try:
            result = subprocess.run(
                ['wmic', 'path', 'win32_VideoController', 'get', 'name'],
                capture_output=True, text=True, timeout=2
            )
            gpu_info = result.stdout.lower()
            
            if 'nvidia' in gpu_info:
                print("[mpv] NVIDIA GPU detected")
                return 'nvdec'  # mpv's NVIDIA decoder
            elif 'intel' in gpu_info:
                print("[mpv] Intel GPU detected")
                return 'd3d11va'  # mpv handles this better than FFplay
            elif 'amd' in gpu_info or 'radeon' in gpu_info:
                print("[mpv] AMD GPU detected")
                return 'd3d11va'
        except Exception as e:
            print(f"[mpv] GPU detection failed: {e}")
        
        # Default to auto (mpv is smarter at detecting)
        print("[mpv] Using auto-detection")
        return 'auto'
    
    def _build_mpv_command(self, mpv_path: str, hw_accel: str) -> list:
        """Build mpv command with appropriate hardware acceleration."""
        
        cmd = [
            mpv_path,
            
            # Input from stdin
            '-',
            
            # === HARDWARE ACCELERATION ===
            f'--hwdec={hw_accel}',  # Hardware decoding
            '--hwdec-codecs=h264',  # Only use hwdec for H.264
            
            # === LOW LATENCY SETTINGS ===
            '--profile=low-latency',  # Built-in low-latency profile
            '--no-cache',  # Disable cache for real-time streaming
            '--untimed',  # Don't sync to system clock
            '--no-demuxer-thread',  # No separate demuxer thread
            '--vd-lavc-threads=1',  # Single decode thread for lowest latency
            '--opengl-glfinish=yes',  # Force GL finish for immediate display
            '--opengl-swapinterval=0',  # No vsync delay
            
            # === DEMUXER SETTINGS ===
            '--demuxer=rawvideo',  # Raw video demuxer
            '--demuxer-rawvideo-codec=h264',  # H.264 codec
            
            # === VIDEO OUTPUT ===
            '--vo=gpu',  # GPU-based video output (best for hw accel)
            '--gpu-api=d3d11',  # Use Direct3D 11 on Windows
            '--gpu-context=win',  # Windows context
            
            # === WINDOW SETTINGS ===
            f'--title={self._title}',
            '--ontop',  # Always on top
            '--no-border',  # Borderless for cleaner look
            '--autofit=30%',  # Start at 30% of screen size
            '--keepaspect',  # Maintain aspect ratio
            
            # === PLAYBACK SETTINGS ===
            '--no-audio',  # Disable audio (handled separately)
            '--no-osc',  # No on-screen controller
            '--no-osd-bar',  # No OSD progress bar
            '--cursor-autohide=100',  # Hide cursor after 100ms
            
            # === INPUT SETTINGS ===
            '--demuxer-max-bytes=2M',  # Small buffer (2MB for ~1 second at 25fps)
            '--demuxer-readahead-secs=0.1',  # Minimal readahead
            
            # === PERFORMANCE ===
            '--video-sync=display-desync',  # Don't sync to display refresh
            '--interpolation=no',  # No frame interpolation
            '--framedrop=vo',  # Drop frames if behind
            
            # === DEBUG ===
            '--msg-level=all=info',  # Info logging
        ]
        
        return cmd
    mpv
    def set_sps(self, sps: bytes):
        """Set Sequence Parameter Set."""
        if not sps.startswith(b'\x00\x00\x00\x01') and not sps.startswith(b'\x00\x00\x01'):
            sps = b'\x00\x00\x00\x01' + sps
        self._sps = sps
        print(f"[FFplay] SPS: {len(sps)} bytes")
        
        # Start if we have both SPS and PPS
        if self._pps and not self._running:
            self.start()
            mpv
    def set_pps(self, pps: bytes):
        """Set Picture Parameter Set."""
        if not pps.startswith(b'\x00\x00\x00\x01') and not pps.startswith(b'\x00\x00\x01'):
            pps = b'\x00\x00\x00\x01' + pps
        self._pps = pps
        print(f"[FFplay] PPS: {len(pps)} bytes")
        
        # Start mpv now that we have both SPS and PPS
        if self._sps and not self._running:
            if self.start():
                # Don't flush buffer immediately - let frames flow naturally
                # This prevents overwhelming mpv during initialization
                print(f"[mpv] {len(self._frame_buffer)} frames buffered, will send gradually")
    
    def start(self) -> bool:
        """Start mpv subprocess with hardware acceleration detection."""
        if self._running:
            return True
            
        # Check if mpv is available
        mpv_path = shutil.which('mpv')
        if not mpv_path:
            print("[mpv] ERROR: mpv not found in PATH")
            print("[mpv] Please install mpv: https://mpv.io/installation/")
            return False
        
        # Detect available hardware acceleration
        hw_accel = self._detect_hardware_accel()
        print(f"[mpv] Using hardware acceleration: {hw_accel}")
            
        self._running = True
        
        # Build command based on detected hardware
        cmd = self._build_mpv_command(mpv_path, hw_accel)
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0
            )mpv] Started (PID: {self._process.pid})")
            
        except Exception as e:
            print(f"[mpv] Failed to start: {e}")
            self._running = False
            return False
            
        # Start stderr reader
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        
        # Give mpv a moment to initialize
        import time
        time.sleep(0.1)  # 100ms for mpv
        time.sleep(0.15)  # 150ms for hardware decoder initialization
        
        # Send SPS/PPS if we have them
        if self._sps and self._pps:
            self._send_config()
            
        return True
        
    def _send_config(self):mpv."""
        if self._config_sent or not self._sps or not self._pps:
            return
            
        try:
            if self._process and self._process.stdin:
                # Send SPS and PPS as separate NAL units
                self._process.stdin.write(self._sps)
                self._process.stdin.write(self._pps)
                # Flush immediately to ensure mpv gets config
                self._process.stdin.flush()
                self._config_sent = True
                print("[mpv] Sent SPS/PPS config")
                # Reset frame count after config
                self._frame_count = 0
                
                # mpv initializes faster than FFplay
                import time
                time.sleep(0.05)  # 50ms for mpv to set up video pipeline
                self._ready = True
                print("[mpv] Ready for video frames")
                
        except Exception as e:
            print(f"[mpv e:
            print(f"[FFplay] Config send error: {e}")
            self._running = False
            
    def decode(self, h264_datampv for decoding and display."""
        # Buffer frames if mpvlay for decoding and display."""
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
                except Exceptmpv e:
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
        except (Brokempvrror, OSError) as e:
            print(f"[FFplay] Pipe error: {e}")
            self._running = False
            self._ready = False
            
    def _flush_buffer(self):mpv after it's ready."""
        with self._lock:
            if not self._frame_buffer:
                return
            
            print(f"[mpv] Flushing {len(self._frame_buffer)} buffered frames")
            for frame_data in self._frame_buffer:
                try:
                    if self._process and self._process.stdin:
                        self._process.stdin.write(frame_data)
                except Exception as e:
                    print(f"[mpv] Buffer flush error: {e}")
                    break
            
            # Clear buffer and flush pipe
            self._frame_buffer.clear()
            try:
                if self._process and self._process.stdin:
                    self._process.stdin.flush()
            except Exception:
                pass
    
    def _read_stderr(self):
        """Read mpv stderr for diagnostics."""
        while self._running:
            try:
                if not self._process or not self._process.stderr:
                    break
                line = self._process.stderr.readline()
                if line:
                    msg = line.decode('utf-8', errors='ignore').strip()
                    if msg:
                        print(f"[mpv] {msg}")
            except Exception:
                break
                
    def stop(self):
        """Stop mpv."""
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
            
        print("[mpv] Stopped")
        
    # Compatibility methods for main_window.py
    def set_frame_callback(self, callback):
        """No-op for compatibility - mpv handles display."""
        pass
        
    def set_resolution_callback(self, callback):
        """No-op for compatibility - mpvack):
        """No-op for compatibility - FFplay auto-detects resolution."""
        pass
