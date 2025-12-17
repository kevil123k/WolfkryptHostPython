"""
Wolfkrypt Host - Screen Mirror Application

Entry point for the application.
"""

import sys

from PyQt6.QtWidgets import QApplication

# Use absolute imports for PyInstaller compatibility
from src.ui import MainWindow


def main():
    """Application entry point."""
    app = QApplication(sys.argv)
    app.setApplicationName("Wolfkrypt Host")
    app.setOrganizationName("Wolfkrypt")
    
    # Set dark theme
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
