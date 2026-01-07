"""
SDL2 Hardware-Accelerated Video Window.

This module provides a separate SDL2 window for displaying video frames
with hardware acceleration. It runs in its own thread to avoid blocking
the main UI.
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
    print("[SDLVideo] Warning: PySDL2 not available - video display disabled")


class SDLVideoWindow:
    """
    Separate SDL2 window for video display with hardware acceleration.
    
    Features:
    - Hardware-accelerated rendering via GPU
    - YUV420P native format (no CPU color conversion)
    - Runs in separate thread for low latency
    - Automatic window resizing
    - VSync support
    """
    
    def __init__(self, title: str = "Wolfkrypt Mirror", width: int = 1920, height: int = 1080):
        """
        Initialize the SDL video window.
        
        Args:
            title: Window title
            width: Initial window width
            height: Initial window height
        """
        self._title = title
        self._width = width
        self._height = height
        self._texture_width = width
        self._texture_height = height
        
        self._window = None
        self._renderer = None
        self._texture = None
        self._running = False
        self._initialized = False
        
        self._thread: Optional[threading.Thread] = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=3)  # Small buffer for low latency
        self._lock = threading.Lock()
        
    @property
    def is_running(self) -> bool:
        return self._running
        
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
            # Initialize SDL video subsystem
            if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) < 0:
                error = sdl2.SDL_GetError()
                print(f"[SDLVideo] Failed to init SDL: {error}")
                self._running = False
                return
                
            # Create window
            self._window = sdl2.SDL_CreateWindow(
                self._title.encode('utf-8'),
                sdl2.SDL_WINDOWPOS_CENTERED,
                sdl2.SDL_WINDOWPOS_CENTERED,
                self._width,
                self._height,
                sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_RESIZABLE | sdl2.SDL_WINDOW_ALLOW_HIGHDPI
            )
            
            if not self._window:
                error = sdl2.SDL_GetError()
                print(f"[SDLVideo] Failed to create window: {error}")
                self._running = False
                return
                
            # Create hardware-accelerated renderer with VSync
            self._renderer = sdl2.SDL_CreateRenderer(
                self._window,
                -1,  # Use first available renderer
                sdl2.SDL_RENDERER_ACCELERATED | sdl2.SDL_RENDERER_PRESENTVSYNC
            )
            
            if not self._renderer:
                # Fall back to software renderer
                print("[SDLVideo] Hardware renderer unavailable, using software")
                self._renderer = sdl2.SDL_CreateRenderer(
                    self._window,
                    -1,
                    sdl2.SDL_RENDERER_SOFTWARE
                )
                
            if not self._renderer:
                error = sdl2.SDL_GetError()
                print(f"[SDLVideo] Failed to create renderer: {error}")
                self._running = False
                return
                
            # Get renderer info
            info = sdl2.SDL_RendererInfo()
            sdl2.SDL_GetRendererInfo(self._renderer, ctypes.byref(info))
            renderer_name = info.name.decode('utf-8') if info.name else 'unknown'
            print(f"[SDLVideo] Renderer: {renderer_name}")
            
            # Create YUV texture for direct GPU upload
            self._create_texture(self._texture_width, self._texture_height)
            
            if not self._texture:
                print("[SDLVideo] Failed to create texture")
                self._running = False
                return
                
            self._initialized = True
            print(f"[SDLVideo] Window created: {self._width}x{self._height}")
            
            # Event loop
            event = sdl2.SDL_Event()
            while self._running:
                # Handle SDL events
                while sdl2.SDL_PollEvent(ctypes.byref(event)):
                    if event.type == sdl2.SDL_QUIT:
                        self._running = False
                        break
                    elif event.type == sdl2.SDL_WINDOWEVENT:
                        if event.window.event == sdl2.SDL_WINDOWEVENT_CLOSE:
                            self._running = False
                            break
                            
                # Display frame if available
                try:
                    frame = self._frame_queue.get_nowait()
                    self._display_frame(frame)
                except queue.Empty:
                    # No frame available, just present current state
                    sdl2.SDL_Delay(1)  # Prevent busy loop
                    
        except Exception as e:
            print(f"[SDLVideo] Error in event loop: {e}")
        finally:
            self._cleanup()
            
    def _create_texture(self, width: int, height: int):
        """Create or recreate the YUV texture."""
        with self._lock:
            if self._texture:
                sdl2.SDL_DestroyTexture(self._texture)
                
            # Create YUV420P texture
            self._texture = sdl2.SDL_CreateTexture(
                self._renderer,
                sdl2.SDL_PIXELFORMAT_IYUV,  # YUV420P / I420
                sdl2.SDL_TEXTUREACCESS_STREAMING,
                width,
                height
            )
            
            if self._texture:
                self._texture_width = width
                self._texture_height = height
                print(f"[SDLVideo] Texture created: {width}x{height}")
            else:
                error = sdl2.SDL_GetError()
                print(f"[SDLVideo] Failed to create texture: {error}")
                
    def update_frame(self, yuv_data: bytes, width: int, height: int):
        """
        Queue a YUV420P frame for display.
        
        Args:
            yuv_data: YUV420P frame data (Y plane + U plane + V plane)
            width: Frame width
            height: Frame height
        """
        if not self._running or not self._initialized:
            return
            
        try:
            self._frame_queue.put_nowait((yuv_data, width, height))
        except queue.Full:
            # Drop oldest frame to make room
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait((yuv_data, width, height))
            except queue.Empty:
                pass
                
    def _display_frame(self, frame_data: Tuple[bytes, int, int]):
        """Display a YUV frame on the texture."""
        yuv_data, width, height = frame_data
        
        # Resize texture if dimensions changed
        if width != self._texture_width or height != self._texture_height:
            self._create_texture(width, height)
            if not self._texture:
                return
                
        # Calculate plane sizes
        y_size = width * height
        uv_size = y_size // 4
        
        expected_size = y_size + uv_size * 2
        if len(yuv_data) < expected_size:
            print(f"[SDLVideo] Invalid frame size: {len(yuv_data)} < {expected_size}")
            return
            
        # Extract planes
        y_plane = yuv_data[:y_size]
        u_plane = yuv_data[y_size:y_size + uv_size]
        v_plane = yuv_data[y_size + uv_size:y_size + uv_size * 2]
        
        # Update YUV texture
        with self._lock:
            if not self._texture:
                return
                
            result = sdl2.SDL_UpdateYUVTexture(
                self._texture,
                None,  # Update entire texture
                y_plane, width,           # Y plane and pitch
                u_plane, width // 2,      # U plane and pitch
                v_plane, width // 2       # V plane and pitch
            )
            
            if result < 0:
                error = sdl2.SDL_GetError()
                print(f"[SDLVideo] Failed to update texture: {error}")
                return
                
            # Clear and render
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
        print("[SDLVideo] Cleanup complete")
        
    def stop(self):
        """Stop the SDL window."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            
    def resize(self, width: int, height: int):
        """Resize the window."""
        if self._window:
            sdl2.SDL_SetWindowSize(self._window, width, height)
