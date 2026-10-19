"""Start the Qt workbench with writable streams for windowed builds."""

import os
import sys
import tempfile
from multiprocessing import freeze_support


def _ensure_text_streams():
    """PyInstaller's windowed bootloader sets stdout/stderr to None on Windows.

    Ultralytics/tqdm writes training progress directly to stdout, so give
    libraries a UTF-8 stream before importing the workbench or model stack.
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

# Frozen Windows training and DataLoader processes restart this EXE. Divert
# them before importing Qt or the model stack. Streams must exist first because
# PyInstaller's windowed worker processes otherwise inherit None stdout/stderr.
freeze_support()

from gui.qt_workbench import main

if __name__ == "__main__":
    main()
