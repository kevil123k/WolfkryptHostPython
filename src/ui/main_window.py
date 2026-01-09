"""
Main window for Wolfkrypt Host application.

Uses the new StreamPipeline for low-latency video streaming.
"""

import sys
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStatusBar, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject

# Use absolute imports for PyInstaller compatibility
from src.core import AoaHost, Authenticator, PacketType, parse_header, HEADER_TOTAL_SIZE
from src.core.protocol import ConfigSubtype
from src.core.pipeline import StreamPipeline
from src.media import AudioDecoder
from src.render import AudioPlayer


class StatusSignal(QObject):
    """Signal for thread-safe status updates."""
    update = pyqtSignal(str)


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        
        # Core components
        self._aoa_host = AoaHost()
        self._authenticator = Authenticator()
        self._audio_decoder = AudioDecoder()
        self._audio_player = AudioPlayer()
        
        # Pipeline (replaces FFplayVideo)
        self._pipeline: Optional[StreamPipeline] = None
        
        # State
        self._running = False
        
        # Thread pool for audio decode
        self._audio_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='audio_decode')
        
        # Audio queue for producer-consumer pattern
        self._audio_queue: queue.Queue = queue.Queue(maxsize=50)
        
        # Status signal for thread-safe updates
        self._status_signal = StatusSignal()
        self._status_signal.update.connect(self._update_status)
        
        self._setup_ui()
        self._setup_callbacks()
        self._load_key()
    
    def _setup_ui(self):
        """Set up the user interface."""
        self.setWindowTitle("Wolfkrypt Host")
        self.setMinimumSize(400, 200)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Info label (video is displayed in separate SDL window)
        info_label = QLabel("Video displays in separate hardware-accelerated window")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_label.setStyleSheet("color: gray; padding: 20px;")
        layout.addWidget(info_label)
        
        # Control bar
        control_layout = QHBoxLayout()
        
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect)
        control_layout.addWidget(self._connect_btn)
        
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        self._disconnect_btn.setEnabled(False)
        control_layout.addWidget(self._disconnect_btn)
        
        control_layout.addStretch()
        
        self._status_label = QLabel("Ready")
        control_layout.addWidget(self._status_label)
        
        layout.addLayout(control_layout)
        
        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Waiting for connection...")
    
    def _setup_callbacks(self):
        """Set up component callbacks."""
        self._aoa_host.set_status_callback(
            lambda msg: self._status_signal.update.emit(msg)
        )
        self._audio_decoder.set_sample_callback(self._handle_audio_samples)
    
    def _handle_audio_samples(self, samples, sample_rate: int):
        """Handle decoded audio samples with dynamic sample rate."""
        # Update sample rate if it changed
        if sample_rate != self._audio_player._sample_rate:
            print(f"[Audio] Updating sample rate: {self._audio_player._sample_rate} -> {sample_rate}")
            self._audio_player.set_sample_rate(sample_rate)
        self._audio_player.play(samples)
    
    def _load_key(self):
        """Load private key from default location."""
        key_paths = [
            Path("keys/private.pem"),
            Path.home() / ".wolfkrypt" / "private.pem",
        ]
        
        for path in key_paths:
            if path.exists():
                if self._authenticator.load_private_key(str(path)):
                    self.statusBar().showMessage(f"Key loaded: {path}")
                    return
        
        self.statusBar().showMessage("Warning: No private key found")
    
    def _on_connect(self):
        """Handle connect button click."""
        if not self._authenticator.is_key_loaded:
            QMessageBox.warning(
                self, "Error",
                "Private key not loaded. Please place private.pem in keys/ folder."
            )
            return
        
        self._connect_btn.setEnabled(False)
        self._status_label.setText("Connecting...")
        
        # Initialize and connect in background
        def connect_thread():
            if not self._aoa_host.initialize():
                self._status_signal.update.emit(f"Init failed: {self._aoa_host.last_error}")
                return
            
            if not self._aoa_host.connect_to_device():
                self._status_signal.update.emit(f"Connect failed: {self._aoa_host.last_error}")
                return
            
            # Create and start the pipeline
            self._pipeline = StreamPipeline(
                aoa_host=self._aoa_host,
                authenticator=self._authenticator,
                status_callback=lambda msg: self._status_signal.update.emit(msg)
            )
            
            # Set up audio handling - connect audio packets to decoder
            self._pipeline.set_audio_callback(self._audio_decoder.decode)
            self._pipeline.set_config_callback(self._handle_config)
            
            # Start audio player
            self._audio_player.start()
            
            # Start the pipeline
            self._running = True
            if not self._pipeline.start():
                self._status_signal.update.emit("Failed to start pipeline")
                return
            
            self._status_signal.update.emit("Connected - Streaming")
        
        threading.Thread(target=connect_thread, daemon=True).start()
    
    def _handle_config(self, subtype: int, config_data: bytes):
        """Handle config packets from the pipeline."""
        if subtype == ConfigSubtype.AUDIO_AAC:
            self._audio_decoder.set_config(config_data)
    
    def _on_disconnect(self):
        """Handle disconnect button click."""
        self._running = False
        
        if self._pipeline:
            self._pipeline.stop()
            self._pipeline = None
        
        self._aoa_host.disconnect()
        self._audio_player.stop()
        
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._status_label.setText("Disconnected")
    
    def _update_status(self, message: str):
        """Update status (called on main thread)."""
        self.statusBar().showMessage(message)
        if "Connected" in message or "Streaming" in message:
            self._connect_btn.setEnabled(False)
            self._disconnect_btn.setEnabled(True)
            self._status_label.setText("Connected")
        elif "failed" in message.lower() or "error" in message.lower():
            self._connect_btn.setEnabled(True)
            self._disconnect_btn.setEnabled(False)
            self._status_label.setText("Error")
    
    def closeEvent(self, event):
        """Handle window close."""
        self._running = False
        
        if self._pipeline:
            self._pipeline.stop()
        
        self._aoa_host.disconnect()
        self._audio_player.stop()
        
        # Shutdown thread pool
        self._audio_executor.shutdown(wait=False)
        
        event.accept()
