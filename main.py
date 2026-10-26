"""程序入口：先准备打包环境，再启动 Qt 工作台。"""

import os
import sys
import tempfile
from multiprocessing import freeze_support


def _ensure_text_streams():
    """为 Windows 无控制台打包程序补上 UTF-8 输出流。

    PyInstaller 的窗口模式可能把标准输出设为 None，训练依赖会直接向其写进度。
    因此必须在导入工作台和模型代码前完成此准备。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        log_dir = os.path.join(base, "autoLabel")
        os.makedirs(log_dir, exist_ok=True)
        stream = open(os.path.join(log_dir, "runtime.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, stream)


_ensure_text_streams()

# Windows 打包后的训练和 DataLoader 子进程会重新启动此程序；先分流子进程，
# 再导入 Qt。输出流已在上方初始化，以免子进程继承 None。
freeze_support()

from gui.qt_workbench import main

if __name__ == "__main__":
    main()
