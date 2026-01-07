"""
SDL2 Hardware-Accelerated Video Window.

This module provides a separate SDL2 window for displaying video frames
with hardware acceleration. Supports mobile aspect ratios and fullscreen toggle.
"""

import ctypes
import queue
import threading
from typing import Optional, Tuple

try:
    import sdl2
    import sdl2.ext
    SDL2_AVAILABLE = True
except ImportError:
    SDL2_AVAILABLE = False
    print("[SDLVideo] Warning: PySDL2 not available")


class SDLVideoWindow:
    """
    SDL2 window for video display with hardware acceleration.
    
    Features:
    - Hardware-accelerated GPU rendering
    - YUV420P native format (no CPU color conversion)
    - Mobile aspect ratio support (9:20, 9:16, etc.)
    - Fullscreen toggle (F11 or double-click)
    - Dynamic resolution updates
    """
    
    def __init__(self, title: str = "Wolfkrypt Mirror"):
        """Initialize the SDL video window."""
        self._title = title
        
        # Default to mobile portrait size (will be updated from SPS)
        self._video_width = 1080
        self._video_height = 2400
        
        # Window size (scaled for display)
        self._window_width = 405   # 1080 * 0.375
        self._window_height = 900  # 2400 * 0.375
        
        self._window = None
        self._renderer = None
        self._texture = None
        self._texture_width = 0
        self._texture_height = 0
        
        self._running = False
        self._initialized = False
        self._fullscreen = False
        
        self._thread: Optional[threading.Thread] = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=3)
        self._lock = threading.Lock()
        self._pending_resize: Optional[Tuple[int, int]] = None
        
    @property
    def is_running(self) -> bool:
        return self._running
        
    def set_video_size(self, width: int, height: int):
        """Set the video resolution (updates window to phone-shaped proportions)."""
        self._video_width = width
        self._video_height = height
        
        # Get desktop resolution using SDL
        display_bounds = sdl2.SDL_Rect()
        if sdl2.SDL_GetDisplayBounds(0, ctypes.byref(display_bounds)) == 0:
            desktop_w = display_bounds.w
            desktop_h = display_bounds.h
        else:
            desktop_w = 1920
            desktop_h = 1080
            
        # Target: phone-shaped window (portrait, 9:20 aspect ratio like mobile)
        # Leave margin for taskbar and title bar
        margin = 100
        max_height = desktop_h - margin
        max_width = desktop_w - margin
        
        # Phone aspect ratio (9:20 portrait - typical modern phones)
        phone_aspect = 9 / 20  # width / height
        
        # Calculate window size to fit on screen while maintaining phone aspect ratio
        if max_height * phone_aspect <= max_width:
            # Height limited
            self._window_height = max_height
            self._window_width = int(max_height * phone_aspect)
        else:
            # Width limited
            self._window_width = max_width
            self._window_height = int(max_width / phone_aspect)
        
        # Queue resize for SDL thread
        self._pending_resize = (self._window_width, self._window_height)
        print(f"[SDLVideo] Video: {width}x{height} -> Window: {self._window_width}x{self._window_height} (desktop: {desktop_w}x{desktop_h})")
        
    def start(self) -> bool:
        """Start the SDL window in a separate thread."""
        if not SDL2_AVAILABLE:
            print("[SDLVideo] Cannot start - SDL2 not available")
            return False
            
        if self._running:
            return True
            
        self._running = True
        self._thread = threading.Thread(target=self._run, name="SDL_Video", daemon=True)
        self._thread.start()
        
        # Wait for initialization
        timeout = 2.0
        while timeout > 0 and not self._initialized:
            threading.Event().wait(0.1)
            timeout -= 0.1
            
        return self._initialized
        
    def _run(self):
        """SDL event loop (runs in separate thread)."""
        try:
            if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) < 0:
                print(f"[SDLVideo] Failed to init SDL: {sdl2.SDL_GetError()}")
                self._running = False
                return
                
            # Create resizable window (not fullscreen)
            self._window = sdl2.SDL_CreateWindow(
                self._title.encode('utf-8'),
                sdl2.SDL_WINDOWPOS_CENTERED,
                sdl2.SDL_WINDOWPOS_CENTERED,
                self._window_width,
                self._window_height,
                sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_RESIZABLE
            )
            
            if not self._window:
                print(f"[SDLVideo] Failed to create window: {sdl2.SDL_GetError()}")
                self._running = False
                return
                
            # Create hardware renderer with VSync
            self._renderer = sdl2.SDL_CreateRenderer(
                self._window, -1,
                sdl2.SDL_RENDERER_ACCELERATED | sdl2.SDL_RENDERER_PRESENTVSYNC
            )
            
            if not self._renderer:
                self._renderer = sdl2.SDL_CreateRenderer(self._window, -1, 0)
                
            if not self._renderer:
                print(f"[SDLVideo] Failed to create renderer")
                self._running = False
                return
                
            # Get renderer info
            info = sdl2.SDL_RendererInfo()
            sdl2.SDL_GetRendererInfo(self._renderer, ctypes.byref(info))
            renderer_name = info.name.decode('utf-8') if info.name else 'unknown'
            print(f"[SDLVideo] Window: {self._window_width}x{self._window_height}, Renderer: {renderer_name}")
            
            self._initialized = True
            
            # Event loop
            event = sdl2.SDL_Event()
            while self._running:
                # Handle pending resize
                if self._pending_resize:
                    w, h = self._pending_resize
                    self._pending_resize = None
                    sdl2.SDL_SetWindowSize(self._window, w, h)
                    
                # Handle SDL events
                while sdl2.SDL_PollEvent(ctypes.byref(event)):
                    if event.type == sdl2.SDL_QUIT:
                        self._running = False
                        break
                    elif event.type == sdl2.SDL_WINDOWEVENT:
                        if event.window.event == sdl2.SDL_WINDOWEVENT_CLOSE:
                            self._running = False
                            break
                    elif event.type == sdl2.SDL_KEYDOWN:
                        # F11 for fullscreen toggle
                        if event.key.keysym.sym == sdl2.SDLK_F11:
                            self._toggle_fullscreen()
                        # ESC to exit fullscreen
                        elif event.key.keysym.sym == sdl2.SDLK_ESCAPE and self._fullscreen:
                            self._toggle_fullscreen()
                    elif event.type == sdl2.SDL_MOUSEBUTTONDOWN:
                        # Double-click for fullscreen
                        if event.button.clicks == 2:
                            self._toggle_fullscreen()
                            
                # Display frame if available
                try:
                    frame = self._frame_queue.get_nowait()
                    self._display_frame(frame)
                except queue.Empty:
                    sdl2.SDL_Delay(1)
                    
        except Exception as e:
            print(f"[SDLVideo] Error: {e}")
        finally:
            self._cleanup()
            
    def _toggle_fullscreen(self):
        """Toggle between fullscreen and windowed mode."""
        if self._fullscreen:
            sdl2.SDL_SetWindowFullscreen(self._window, 0)
            self._fullscreen = False
            print("[SDLVideo] Windowed mode")
        else:
            sdl2.SDL_SetWindowFullscreen(self._window, sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP)
            self._fullscreen = True
            print("[SDLVideo] Fullscreen mode (F11 or ESC to exit)")
            
    def _create_texture(self, width: int, height: int):
        """Create or recreate the YUV texture."""
        with self._lock:
            if self._texture:
                sdl2.SDL_DestroyTexture(self._texture)
                
            self._texture = sdl2.SDL_CreateTexture(
                self._renderer,
                sdl2.SDL_PIXELFORMAT_IYUV,
                sdl2.SDL_TEXTUREACCESS_STREAMING,
                width, height
            )
            
            if self._texture:
                self._texture_width = width
                self._texture_height = height
                print(f"[SDLVideo] Texture: {width}x{height}")
            else:
                print(f"[SDLVideo] Failed to create texture")
                
    def update_frame(self, yuv_data: bytes, width: int, height: int):
        """Queue a YUV420P frame for display."""
        if not self._running or not self._initialized:
            return
            
        try:
            self._frame_queue.put_nowait((yuv_data, width, height))
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait((yuv_data, width, height))
            except queue.Empty:
                pass
                
    def _display_frame(self, frame_data: Tuple[bytes, int, int]):
        """Display a YUV frame."""
        yuv_data, width, height = frame_data
        
        # Create/resize texture if needed
        if width != self._texture_width or height != self._texture_height:
            self._create_texture(width, height)
            
            # Also update window size on first frame
            if not self._fullscreen:
                self.set_video_size(width, height)
                
            if not self._texture:
                return
                
        # Calculate plane sizes
        y_size = width * height
        uv_size = y_size // 4
        
        expected = y_size + uv_size * 2
        if len(yuv_data) < expected:
            return
            
        y_plane = yuv_data[:y_size]
        u_plane = yuv_data[y_size:y_size + uv_size]
        v_plane = yuv_data[y_size + uv_size:]
        
        with self._lock:
            if not self._texture:
                return
                
            result = sdl2.SDL_UpdateYUVTexture(
                self._texture, None,
                y_plane, width,
                u_plane, width // 2,
                v_plane, width // 2
            )
            
            if result < 0:
                return
                
            sdl2.SDL_RenderClear(self._renderer)
            sdl2.SDL_RenderCopy(self._renderer, self._texture, None, None)
            sdl2.SDL_RenderPresent(self._renderer)
            
    def _cleanup(self):
        """Clean up SDL resources."""
        with self._lock:
            if self._texture:
                sdl2.SDL_DestroyTexture(self._texture)
                self._texture = None
            if self._renderer:
                sdl2.SDL_DestroyRenderer(self._renderer)
                self._renderer = None
            if self._window:
                sdl2.SDL_DestroyWindow(self._window)
                self._window = None
                
        sdl2.SDL_Quit()
        self._initialized = False
        print("[SDLVideo] Closed")
        
    def stop(self):
        """Stop the SDL window."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
