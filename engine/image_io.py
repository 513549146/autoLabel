"""共享的图片读取与第三方模型临时工作目录保护。"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import cv2

_WORKING_DIRECTORY_LOCK = threading.RLock()


@contextmanager
def _working_directory(path):
    """串行切换工作目录，确保第三方库找到本地权重，并在结束后恢复。"""
    with _WORKING_DIRECTORY_LOCK:
        previous = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(previous)


def _imread_unicode(path):
    """读取图片，兼容含中文等非 ASCII 字符的路径（cv2.imread 在 Windows 上无法处理）"""
    import numpy as np

    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)
