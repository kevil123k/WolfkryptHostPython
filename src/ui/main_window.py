"""
Main window for Wolfkrypt Host application.
"""

import sys
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QStatusBar, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject

from ..core import AoaHost, Authenticator, PacketType, parse_header, HEADER_TOTAL_SIZE
from ..core.protocol import ConfigSubtype
from ..media import VideoDecoder, AudioDecoder
from ..render import VideoWindow, AudioPlayer


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
        self._video_decoder = VideoDecoder()
        self._audio_decoder = AudioDecoder()
        self._audio_player = AudioPlayer()
        
        # State
        self._running = False
        self._receive_thread: Optional[threading.Thread] = None
        
        # Status signal for thread-safe updates
        self._status_signal = StatusSignal()
        self._status_signal.update.connect(self._update_status)
        
        self._setup_ui()
        self._setup_callbacks()
        self._load_key()
    
    def _setup_ui(self):
        """Set up the user interface."""
        self.setWindowTitle("Wolfkrypt Host")
        self.setMinimumSize(800, 600)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Video display
        self._video_window = VideoWindow()
        layout.addWidget(self._video_window, 1)
        
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
        self._video_decoder.set_frame_callback(self._video_window.show_frame)
        self._audio_decoder.set_sample_callback(
            lambda samples, rate: self._audio_player.play(samples)
        )
    
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
            
            # Start audio and receive loop
            self._audio_player.start()
            self._running = True
            self._receive_loop()
        
        threading.Thread(target=connect_thread, daemon=True).start()
    
    def _on_disconnect(self):
        """Handle disconnect button click."""
        self._running = False
        self._aoa_host.disconnect()
        self._audio_player.stop()
        self._video_window.clear()
        
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._status_label.setText("Disconnected")
    
    def _receive_loop(self):
        """Main receive loop for USB data."""
        self._disconnect_btn.setEnabled(True)
        buffer = bytearray()
        
        while self._running and self._aoa_host.is_connected:
            # Read data
            data = self._aoa_host.read(16384, timeout_ms=100)
            if data is None:
                break
            if len(data) == 0:
                continue
            
            buffer.extend(data)
            
            # Process complete packets
            while len(buffer) >= HEADER_TOTAL_SIZE:
                header = parse_header(bytes(buffer[:HEADER_TOTAL_SIZE]))
                if not header:
                    break
                
                total_size = HEADER_TOTAL_SIZE + header.length
                if len(buffer) < total_size:
                    break
                
                # Extract payload
                payload = bytes(buffer[HEADER_TOTAL_SIZE:total_size])
                buffer = buffer[total_size:]
                
                # Handle packet
                self._handle_packet(header.type, payload)
        
        self._status_signal.update.emit("Connection closed")
    
    def _handle_packet(self, packet_type: PacketType, payload: bytes):
        """Handle a received packet."""
        if packet_type == PacketType.VIDEO:
            self._video_decoder.decode(payload)
        
        elif packet_type == PacketType.AUDIO:
            self._audio_decoder.decode(payload)
        
        elif packet_type == PacketType.CONFIG:
            if len(payload) < 1:
                return
            subtype = payload[0]
            config_data = payload[1:]
            
            if subtype == ConfigSubtype.VIDEO_SPS:
                self._video_decoder.set_sps(config_data)
            elif subtype == ConfigSubtype.VIDEO_PPS:
                self._video_decoder.set_pps(config_data)
            elif subtype == ConfigSubtype.AUDIO_AAC:
                self._audio_decoder.set_config(config_data)
        
        elif packet_type == PacketType.AUTH_CHALLENGE:
            # Sign challenge and send response
            signature = self._authenticator.sign_challenge(payload)
            if signature:
                from ..core.protocol import create_header
                response = create_header(PacketType.AUTH_RESPONSE, len(signature)) + signature
                self._aoa_host.write(response)
        
        elif packet_type == PacketType.AUTH_SUCCESS:
            self._status_signal.update.emit("Authentication successful")
        
        elif packet_type == PacketType.AUTH_FAIL:
            self._status_signal.update.emit("Authentication failed")
            self._running = False
    
    def _update_status(self, message: str):
        """Update status (called on main thread)."""
        self.statusBar().showMessage(message)
        if "Connected" in message:
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
        self._aoa_host.disconnect()
        self._audio_player.stop()
        event.accept()
