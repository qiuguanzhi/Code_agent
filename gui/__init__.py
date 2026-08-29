"""Public exports for the optional PySide6 desktop interface."""

from gui.main_window import MainWindow
from gui.splash_screen import SplashScreen
from gui.worker import AgentWorker

__all__ = ["AgentWorker", "MainWindow", "SplashScreen"]
