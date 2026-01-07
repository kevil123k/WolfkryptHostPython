"""
H.264 Video Decoder using FFmpeg subprocess.

This module uses FFmpeg as a subprocess to decode H.264 video streams.
It handles NAL unit framing properly and supports hardware acceleration.
"""

import subprocess
import threading
import queue
import shutil
from typing import Optional, Callable


class VideoDecoder:
    """
    Decodes H.264 using FFmpeg subprocess with hardware acceleration.
    
    This approach:
    - Properly handles Annex B NAL unit framing
    - Supports hardware decoders (CUDA, DXVA2, QSV)
    - Outputs YUV420P for direct GPU texture upload
    - Runs decode in separate threads for low latency
    """
    
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._width = 0
        self._height = 0
        self._frame_callback: Optional[Callable[[bytes, int, int], None]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._writer_thread: Optional[threading.Thread] = None
        self._write_queue: queue.Queue = queue.Queue(maxsize=100)
        self._decoder_name = ""
        self._frames_decoded = 0
        
        # SPS/PPS for decoder initialization
        self._sps: Optional[bytes] = None
        self._pps: Optional[bytes] = None
        self._config_sent = False
        
    def set_frame_callback(self, callback: Callable[[bytes, int, int], None]):
        """
        Set callback for decoded frames.
        
        Args:
            callback: Function called with (yuv_data, width, height) for each frame
        """
        self._frame_callback = callback
        
    def set_sps(self, sps: bytes):
        """Set Sequence Parameter Set and parse resolution."""
        # Add start code if missing
        if not sps.startswith(b'\x00\x00\x00\x01') and not sps.startswith(b'\x00\x00\x01'):
            sps = b'\x00\x00\x00\x01' + sps
        self._sps = sps
        
        # Try to parse resolution from SPS
        width, height = self._parse_sps_resolution(sps)
        if width > 0 and height > 0:
            print(f"[VideoDecoder] SPS parsed: {width}x{height}")
            if not self._running:
                self.start(width, height)
                
    def set_pps(self, pps: bytes):
        """Set Picture Parameter Set."""
        # Add start code if missing
        if not pps.startswith(b'\x00\x00\x00\x01') and not pps.startswith(b'\x00\x00\x01'):
            pps = b'\x00\x00\x00\x01' + pps
        self._pps = pps
        print(f"[VideoDecoder] PPS received: {len(pps)} bytes")
        
    def _parse_sps_resolution(self, sps: bytes) -> tuple:
        """
        Parse resolution from H.264 SPS NAL unit.
        Returns (width, height) or (0, 0) on failure.
        """
        try:
            # This is a simplified parser - in production you'd use proper SPS parsing
            # For now, we'll rely on FFmpeg to detect the resolution
            # Return 0,0 to let FFmpeg auto-detect
            return (0, 0)
        except Exception:
            return (0, 0)
            
    def start(self, width: int = 0, height: int = 0) -> bool:
        """
        Start the FFmpeg decoder process.
        
        Args:
            width: Video width (0 for auto-detect)
            height: Video height (0 for auto-detect)
        """
        if self._running:
            return True
            
        # Check if FFmpeg is available
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            print("[VideoDecoder] ERROR: FFmpeg not found in PATH")
            print("[VideoDecoder] Please install FFmpeg: https://ffmpeg.org/download.html")
            return False
            
        # Use default resolution if not specified
        if width <= 0 or height <= 0:
            # Will be updated when we receive first frame
            width = 1920
            height = 1080
            
        self._width = width
        self._height = height
        self._running = True
        
        # Build FFmpeg command
        # Try hardware decoders in order of preference
        cmd = [
            ffmpeg_path,
            '-loglevel', 'warning',
            '-hwaccel', 'auto',           # Let FFmpeg pick best hardware decoder
            '-f', 'h264',                  # Input format: raw H.264 Annex B
            '-i', 'pipe:0',                # Read from stdin
            '-f', 'rawvideo',              # Output format: raw video
            '-pix_fmt', 'yuv420p',         # YUV420P for SDL texture
            '-an',                         # No audio
            '-sn',                         # No subtitles
            'pipe:1'                       # Write to stdout
        ]
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # Unbuffered for low latency
            )
            print(f"[VideoDecoder] Started FFmpeg (expecting {width}x{height})")
            
        except Exception as e:
            print(f"[VideoDecoder] Failed to start FFmpeg: {e}")
            self._running = False
            return False
            
        # Start reader thread
        self._reader_thread = threading.Thread(
            target=self._read_frames,
            name="FFmpeg_Reader",
            daemon=True
        )
        self._reader_thread.start()
        
        # Start writer thread
        self._writer_thread = threading.Thread(
            target=self._write_data,
            name="FFmpeg_Writer",
            daemon=True
        )
        self._writer_thread.start()
        
        # Start stderr reader for diagnostics
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name="FFmpeg_Stderr",
            daemon=True
        )
        self._stderr_thread.start()
        
        return True
        
    def decode(self, h264_data: bytes):
        """
        Queue H.264 data for decoding.
        
        Args:
            h264_data: Raw H.264 NAL unit(s) in Annex B format
        """
        if not self._running:
            # Auto-start if not running
            if self._sps and self._pps:
                self.start()
            else:
                return
                
        if not self._process:
            return
            
        # Send SPS/PPS first if not sent yet
        if not self._config_sent and self._sps and self._pps:
            try:
                self._write_queue.put_nowait(self._sps)
                self._write_queue.put_nowait(self._pps)
                self._config_sent = True
            except queue.Full:
                pass
                
        try:
            self._write_queue.put_nowait(h264_data)
        except queue.Full:
            # Drop frame if queue is full (prevents latency buildup)
            pass
            
    def _write_data(self):
        """Writer thread - sends H.264 data to FFmpeg stdin."""
        while self._running:
            try:
                data = self._write_queue.get(timeout=0.1)
                if self._process and self._process.stdin:
                    try:
                        self._process.stdin.write(data)
                        self._process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        print("[VideoDecoder] FFmpeg stdin closed")
                        break
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[VideoDecoder] Write error: {e}")
                break
                
    def _read_frames(self):
        """Reader thread - reads decoded YUV frames from FFmpeg stdout."""
        # Initial frame size (will be updated when we detect resolution)
        frame_size = self._width * self._height * 3 // 2  # YUV420P
        
        while self._running:
            try:
                if not self._process or not self._process.stdout:
                    break
                    
                # Read one frame
                yuv_data = self._process.stdout.read(frame_size)
                
                if len(yuv_data) == 0:
                    # FFmpeg closed
                    break
                    
                if len(yuv_data) == frame_size:
                    self._frames_decoded += 1
                    
                    if self._frames_decoded == 1:
                        print(f"[VideoDecoder] First frame decoded: {self._width}x{self._height}")
                        
                    if self._frame_callback:
                        self._frame_callback(yuv_data, self._width, self._height)
                else:
                    # Partial frame - may indicate resolution change
                    print(f"[VideoDecoder] Partial frame: {len(yuv_data)} bytes (expected {frame_size})")
                    
            except Exception as e:
                if self._running:
                    print(f"[VideoDecoder] Read error: {e}")
                break
                
        print(f"[VideoDecoder] Reader stopped (decoded {self._frames_decoded} frames)")
        
    def _read_stderr(self):
        """Read FFmpeg stderr for diagnostics."""
        while self._running:
            try:
                if not self._process or not self._process.stderr:
                    break
                    
                line = self._process.stderr.readline()
                if line:
                    msg = line.decode('utf-8', errors='ignore').strip()
                    if msg:
                        # Check for resolution info
                        if 'x' in msg and ('Video' in msg or 'Stream' in msg):
                            print(f"[FFmpeg] {msg}")
                        elif 'error' in msg.lower() or 'warning' in msg.lower():
                            print(f"[FFmpeg] {msg}")
            except Exception:
                break
                
    def stop(self):
        """Stop the decoder."""
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
            
        # Wait for threads
        for thread in [self._writer_thread, self._reader_thread]:
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
                
        print(f"[VideoDecoder] Stopped (total frames: {self._frames_decoded})")
        
    def reset(self):
        """Reset the decoder (stop and allow restart)."""
        self.stop()
        self._config_sent = False
        self._frames_decoded = 0
        
    def set_resolution(self, width: int, height: int):
        """
        Update expected resolution.
        
        Call this if you know the resolution changed.
        """
        if width != self._width or height != self._height:
            print(f"[VideoDecoder] Resolution changed: {self._width}x{self._height} -> {width}x{height}")
            self._width = width
            self._height = height
            # Restart decoder with new resolution
            self.stop()
            self.start(width, height)
