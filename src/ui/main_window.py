"""
Main window for Wolfkrypt Host application.

Uses the new StreamBridge (MPV subprocess) for low-latency video streaming.
"""

import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStatusBar, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject

# Use absolute imports for PyInstaller compatibility
from src.core import AoaHost, Authenticator
from src.core.protocol import ConfigSubtype
from src.core.stream_bridge import StreamBridge
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
        
        # Stream bridge (replaces StreamPipeline)
        self._bridge: Optional[StreamBridge] = None
        
        # State
        self._running = False
        
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
        
        # Info label
        info_label = QLabel("Video displays in MPV window (hardware accelerated)")
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
        """Handle decoded audio samples."""
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
            
            # Create the stream bridge (MPV-based)
            self._bridge = StreamBridge(
                aoa_host=self._aoa_host,
                authenticator=self._authenticator,
                status_callback=lambda msg: self._status_signal.update.emit(msg)
            )
            
            # Set up audio handling
            self._bridge.set_audio_callback(self._audio_decoder.decode)
            self._bridge.set_config_callback(self._handle_config)
            
            # Start audio player
            self._audio_player.start()
            
            # Start the bridge
            self._running = True
            if not self._bridge.start():
                self._status_signal.update.emit("Failed to start stream bridge")
                return
            
            self._status_signal.update.emit("Connected - Streaming via MPV")
        
        threading.Thread(target=connect_thread, daemon=True).start()
    
    def _handle_config(self, subtype: int, config_data: bytes):
        """Handle config packets from the bridge."""
        if subtype == ConfigSubtype.AUDIO_AAC:
            self._audio_decoder.set_config(config_data)
    
    def _on_disconnect(self):
        """Handle disconnect button click."""
        self._running = False
        
        if self._bridge:
            self._bridge.stop()
            self._bridge = None
        
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
        
        if self._bridge:
            self._bridge.stop()
        
        self._aoa_host.disconnect()
        self._audio_player.stop()
        
        event.accept()
