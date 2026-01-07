"""
H.264 Video Decoder using FFmpeg subprocess.

This module uses FFmpeg as a subprocess to decode H.264 video streams.
It handles NAL unit framing properly and supports hardware acceleration.
"""

import subprocess
import threading
import queue
import shutil
import struct
from typing import Optional, Callable, Tuple


class VideoDecoder:
    """
    Decodes H.264 using FFmpeg subprocess with hardware acceleration.
    
    This approach:
    - Properly handles Annex B NAL unit framing
    - Supports hardware decoders (CUDA, DXVA2, QSV)
    - Outputs YUV420P for direct GPU texture upload
    - Parses SPS for dynamic resolution detection
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
        self._resolution_callback: Optional[Callable[[int, int], None]] = None
        
    def set_frame_callback(self, callback: Callable[[bytes, int, int], None]):
        """Set callback for decoded frames (yuv_data, width, height)."""
        self._frame_callback = callback
        
    def set_resolution_callback(self, callback: Callable[[int, int], None]):
        """Set callback for when resolution is detected."""
        self._resolution_callback = callback
        
    def set_sps(self, sps: bytes):
        """Set Sequence Parameter Set and parse resolution."""
        # Add start code if missing
        if not sps.startswith(b'\x00\x00\x00\x01') and not sps.startswith(b'\x00\x00\x01'):
            sps = b'\x00\x00\x00\x01' + sps
        self._sps = sps
        
        # Parse resolution from SPS
        width, height = self._parse_sps_resolution(sps)
        if width > 0 and height > 0:
            self._width = width
            self._height = height
            print(f"[VideoDecoder] SPS parsed: {width}x{height}")
            
            # Notify about resolution
            if self._resolution_callback:
                self._resolution_callback(width, height)
            
            # Start decoder if we have PPS too
            if self._pps and not self._running:
                self.start(width, height)
        else:
            print(f"[VideoDecoder] SPS received: {len(sps)} bytes (resolution parse failed, using default)")
                
    def set_pps(self, pps: bytes):
        """Set Picture Parameter Set."""
        if not pps.startswith(b'\x00\x00\x00\x01') and not pps.startswith(b'\x00\x00\x01'):
            pps = b'\x00\x00\x00\x01' + pps
        self._pps = pps
        print(f"[VideoDecoder] PPS received: {len(pps)} bytes")
        
        # Start decoder if we have SPS and resolution
        if self._sps and self._width > 0 and self._height > 0 and not self._running:
            self.start(self._width, self._height)
        
    def _parse_sps_resolution(self, sps: bytes) -> Tuple[int, int]:
        """
        Parse resolution from H.264 SPS NAL unit.
        Returns (width, height) or (0, 0) on failure.
        """
        try:
            # Find NAL unit data after start code
            if sps.startswith(b'\x00\x00\x00\x01'):
                data = sps[5:]  # Skip start code + NAL header
            elif sps.startswith(b'\x00\x00\x01'):
                data = sps[4:]  # Skip start code + NAL header
            else:
                data = sps[1:]  # Skip NAL header
                
            if len(data) < 4:
                return (0, 0)
                
            # Simple SPS parsing - read profile, level, then find pic_width/height
            # This is a simplified parser for common cases
            
            # Use bitstream reader
            reader = BitReader(data)
            
            # profile_idc
            profile_idc = reader.read_bits(8)
            # constraint flags + reserved
            reader.read_bits(8)
            # level_idc
            level_idc = reader.read_bits(8)
            # seq_parameter_set_id
            reader.read_ue()
            
            # Handle high profile scaling lists
            if profile_idc in [100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135]:
                chroma_format_idc = reader.read_ue()
                if chroma_format_idc == 3:
                    reader.read_bits(1)  # separate_colour_plane_flag
                reader.read_ue()  # bit_depth_luma_minus8
                reader.read_ue()  # bit_depth_chroma_minus8
                reader.read_bits(1)  # qpprime_y_zero_transform_bypass_flag
                
                seq_scaling_matrix_present_flag = reader.read_bits(1)
                if seq_scaling_matrix_present_flag:
                    for i in range(8 if chroma_format_idc != 3 else 12):
                        if reader.read_bits(1):  # seq_scaling_list_present_flag
                            # Skip scaling list
                            size = 16 if i < 6 else 64
                            last_scale = 8
                            next_scale = 8
                            for j in range(size):
                                if next_scale != 0:
                                    delta_scale = reader.read_se()
                                    next_scale = (last_scale + delta_scale + 256) % 256
                                last_scale = next_scale if next_scale != 0 else last_scale
            
            # log2_max_frame_num_minus4
            reader.read_ue()
            
            # pic_order_cnt_type
            pic_order_cnt_type = reader.read_ue()
            if pic_order_cnt_type == 0:
                reader.read_ue()  # log2_max_pic_order_cnt_lsb_minus4
            elif pic_order_cnt_type == 1:
                reader.read_bits(1)  # delta_pic_order_always_zero_flag
                reader.read_se()  # offset_for_non_ref_pic
                reader.read_se()  # offset_for_top_to_bottom_field
                num_ref = reader.read_ue()
                for _ in range(num_ref):
                    reader.read_se()
            
            # max_num_ref_frames
            reader.read_ue()
            # gaps_in_frame_num_value_allowed_flag
            reader.read_bits(1)
            
            # pic_width_in_mbs_minus1
            pic_width_in_mbs_minus1 = reader.read_ue()
            # pic_height_in_map_units_minus1
            pic_height_in_map_units_minus1 = reader.read_ue()
            
            # frame_mbs_only_flag
            frame_mbs_only_flag = reader.read_bits(1)
            if not frame_mbs_only_flag:
                reader.read_bits(1)  # mb_adaptive_frame_field_flag
            
            # direct_8x8_inference_flag
            reader.read_bits(1)
            
            # frame_cropping_flag
            frame_cropping_flag = reader.read_bits(1)
            crop_left = crop_right = crop_top = crop_bottom = 0
            if frame_cropping_flag:
                crop_left = reader.read_ue()
                crop_right = reader.read_ue()
                crop_top = reader.read_ue()
                crop_bottom = reader.read_ue()
            
            # Calculate dimensions
            width = (pic_width_in_mbs_minus1 + 1) * 16
            height = (2 - frame_mbs_only_flag) * (pic_height_in_map_units_minus1 + 1) * 16
            
            # Apply cropping
            crop_unit_x = 2 if frame_mbs_only_flag else 2
            crop_unit_y = 2 * (2 - frame_mbs_only_flag)
            
            width -= (crop_left + crop_right) * crop_unit_x
            height -= (crop_top + crop_bottom) * crop_unit_y
            
            return (width, height)
            
        except Exception as e:
            print(f"[VideoDecoder] SPS parse error: {e}")
            return (0, 0)
            
    def start(self, width: int, height: int) -> bool:
        """Start the FFmpeg decoder process."""
        if self._running:
            return True
            
        # Check if FFmpeg is available
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            print("[VideoDecoder] ERROR: FFmpeg not found in PATH")
            return False
            
        self._width = width
        self._height = height
        self._running = True
        
        # Build FFmpeg command
        cmd = [
            ffmpeg_path,
            '-loglevel', 'info',
            '-hwaccel', 'auto',
            '-f', 'h264',
            '-i', 'pipe:0',
            '-f', 'rawvideo',
            '-pix_fmt', 'yuv420p',
            '-an', '-sn',
            'pipe:1'
        ]
        
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )
            print(f"[VideoDecoder] Started FFmpeg for {width}x{height}")
            
        except Exception as e:
            print(f"[VideoDecoder] Failed to start FFmpeg: {e}")
            self._running = False
            return False
            
        # Start threads
        self._reader_thread = threading.Thread(target=self._read_frames, daemon=True)
        self._reader_thread.start()
        
        self._writer_thread = threading.Thread(target=self._write_data, daemon=True)
        self._writer_thread.start()
        
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        
        return True
        
    def decode(self, h264_data: bytes):
        """Queue H.264 data for decoding."""
        if not self._running:
            if self._sps and self._pps and self._width > 0:
                self.start(self._width, self._height)
            else:
                return
                
        if not self._process:
            return
            
        # Send SPS/PPS first
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
                        break
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[VideoDecoder] Write error: {e}")
                break
                
    def _read_frames(self):
        """Reader thread - reads decoded YUV frames from FFmpeg stdout."""
        frame_size = self._width * self._height * 3 // 2
        buffer = bytearray()
        
        print(f"[VideoDecoder] Reader started, frame_size={frame_size} bytes")
        
        while self._running:
            try:
                if not self._process or not self._process.stdout:
                    break
                
                # Read available data (non-blocking read with small chunks)
                chunk = self._process.stdout.read(65536)  # 64KB chunks
                
                if len(chunk) == 0:
                    # EOF - FFmpeg closed
                    print("[VideoDecoder] FFmpeg stdout closed")
                    break
                    
                buffer.extend(chunk)
                
                # Extract complete frames from buffer
                while len(buffer) >= frame_size:
                    yuv_data = bytes(buffer[:frame_size])
                    buffer = buffer[frame_size:]
                    
                    self._frames_decoded += 1
                    
                    if self._frames_decoded == 1:
                        print(f"[VideoDecoder] First frame: {self._width}x{self._height}")
                    elif self._frames_decoded % 60 == 0:
                        print(f"[VideoDecoder] Decoded {self._frames_decoded} frames")
                        
                    if self._frame_callback:
                        self._frame_callback(yuv_data, self._width, self._height)
                        
            except Exception as e:
                if self._running:
                    print(f"[VideoDecoder] Read error: {e}")
                break
                
        print(f"[VideoDecoder] Reader stopped ({self._frames_decoded} frames, {len(buffer)} bytes buffered)")
        
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
                        # Show all FFmpeg messages for debugging
                        print(f"[FFmpeg] {msg}")
            except Exception:
                break
                
    def stop(self):
        """Stop the decoder."""
        self._running = False
        
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
            
        print(f"[VideoDecoder] Stopped")
        
    def reset(self):
        """Reset the decoder."""
        self.stop()
        self._config_sent = False
        self._frames_decoded = 0


class BitReader:
    """Simple bitstream reader for SPS parsing."""
    
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0  # bit position
        
    def read_bits(self, n: int) -> int:
        """Read n bits."""
        result = 0
        for _ in range(n):
            byte_pos = self._pos // 8
            bit_pos = 7 - (self._pos % 8)
            
            if byte_pos >= len(self._data):
                return result
                
            if self._data[byte_pos] & (1 << bit_pos):
                result = (result << 1) | 1
            else:
                result = result << 1
                
            self._pos += 1
        return result
        
    def read_ue(self) -> int:
        """Read unsigned Exp-Golomb coded value."""
        leading_zeros = 0
        while self.read_bits(1) == 0 and leading_zeros < 32:
            leading_zeros += 1
        if leading_zeros == 0:
            return 0
        return (1 << leading_zeros) - 1 + self.read_bits(leading_zeros)
        
    def read_se(self) -> int:
        """Read signed Exp-Golomb coded value."""
        val = self.read_ue()
        if val % 2 == 0:
            return -(val // 2)
        else:
            return (val + 1) // 2
