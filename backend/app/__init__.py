"""ClipForge AI backend."""
import os

__version__ = "0.1.0"

# OpenCV prints a warning about its own inference engine every time the face detector is made (several times
# per render), into the window ClipForge runs in. Only its errors are worth seeing there. Set before OpenCV is
# first loaded; a level you set yourself is kept.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
