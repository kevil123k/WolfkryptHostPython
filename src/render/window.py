"""
Video display window using PyQt6.
"""

from typing import Optional
import numpy as np

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt, pyqtSignal, QObject


class FrameSignal(QObject):
    """Signal for thread-safe frame updates."""
    new_frame = pyqtSignal(np.ndarray)


class VideoWindow(QWidget):
    """Widget for displaying video frames."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        
        # Signal for thread-safe updates
        self._frame_signal = FrameSignal()
        self._frame_signal.new_frame.connect(self._update_frame)
    
    def _setup_ui(self):
        """Set up the UI."""
        self.setWindowTitle("Wolfkrypt Screen Mirror")
        self.setMinimumSize(640, 480)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._video_label = QLabel()
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setStyleSheet("background-color: black;")
        self._video_label.setScaledContents(True)
        layout.addWidget(self._video_label)
    
    def show_frame(self, frame: np.ndarray):
        """Thread-safe method to display a frame."""
        self._frame_signal.new_frame.emit(frame)
    
    def _update_frame(self, frame: np.ndarray):
        """Update the displayed frame (called on main thread)."""
        if frame is None:
            return
        
        height, width, channels = frame.shape
        bytes_per_line = channels * width
        
        # Convert numpy array to QImage
        qimage = QImage(
            frame.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGB888
        )
        
        # Scale to fit while maintaining aspect ratio
        scaled_pixmap = QPixmap.fromImage(qimage).scaled(
            self._video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        
        self._video_label.setPixmap(scaled_pixmap)
    
    def clear(self):
        """Clear the video display."""
        self._video_label.clear()
        self._video_label.setStyleSheet("background-color: black;")
