"""gaze capture — 屏幕截图 + 窗口管理"""
from .screen import screenshot, list_windows, perceptual_hash, hamming_distance, crop_borders, get_foreground_window

__all__ = ['screenshot', 'list_windows', 'perceptual_hash', 'hamming_distance', 'crop_borders', 'get_foreground_window']
