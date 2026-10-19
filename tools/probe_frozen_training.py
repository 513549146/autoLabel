"""Exercise the packaged training worker with a small exported YOLO dataset.

This probes the actual frozen multiprocessing entry point without opening Qt.
It writes training runs only to the --runs directory and never changes the
source images or project model registry.
"""
from __future__ import annotations

import argparse
import multiprocessing
import multiprocessing.popen_spawn_win32 as popen_spawn_win32
import os
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application.training_worker import run_training_process


def stop_tree(process):
    if not process.is_alive():
        return
    parent = psutil.Process(process.pid)
    targets = parent.children(recursive=True) + [parent]
    for target in targets:
        try:
            target.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(targets, timeout=1)
    for target in alive:
        try:
            target.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--stop-after-ready", action="store_true")
    args = parser.parse_args()

    context = multiprocessing.get_context("spawn")
    stop_event = context.Event()
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=run_training_process,
        args=(args.data, args.model, 1, args.runs, stop_event, sender),
        daemon=False,
    )
    # Match the command line that a PyInstaller-frozen parent uses to create
    # a multiprocessing child. Restore the probe interpreter immediately after.
    original_executable = sys.executable
    original_frozen = getattr(sys, "frozen", None)
    original_winenv = popen_spawn_win32.WINENV
    try:
        sys.executable = os.path.abspath(args.exe)
        sys.frozen = True
        multiprocessing.set_executable(sys.executable)
        popen_spawn_win32.WINENV = False
        process.start()
    finally:
        sys.executable = original_executable
        multiprocessing.set_executable(original_executable)
        popen_spawn_win32.WINENV = original_winenv
        if original_frozen is None:
            del sys.frozen
        else:
            sys.frozen = original_frozen
        sender.close()

    started = time.monotonic()
    ready_seconds = None
    terminal = None
    terminal_received_at = None
    try:
        while time.monotonic() - started < args.timeout:
            try:
                available = receiver.poll(1)
            except (BrokenPipeError, OSError):
                if terminal is None:
                    break
                time.sleep(0.1)
                available = False
            if available:
                try:
                    message = receiver.recv()
                except EOFError:
                    if terminal is None:
                        break
                    message = None
                if message is not None:
                    kind, *payload = message
                    if kind == "stage":
                        print(f"{time.monotonic() - started:.1f}s stage: {payload[0]}", flush=True)
                        if "训练与验证数据加载器已就绪" in payload[0] and ready_seconds is None:
                            ready_seconds = time.monotonic() - started
                            if args.stop_after_ready:
                                stop_event.set()
                    elif kind == "batch":
                        print(f"{time.monotonic() - started:.1f}s batch: {payload}", flush=True)
                    elif kind in {"epoch", "complete", "cancelled", "error"}:
                        print(f"{time.monotonic() - started:.1f}s {kind}: {payload}", flush=True)
                        if kind in {"complete", "cancelled", "error"}:
                            terminal = kind
                            terminal_received_at = time.monotonic()
            if not process.is_alive():
                break
            if terminal_received_at is not None and time.monotonic() - terminal_received_at >= 2:
                print("terminal received; cleaning up remaining worker processes", flush=True)
                stop_tree(process)
                break
        if process.is_alive():
            print("timeout: terminating training process tree", flush=True)
            stop_tree(process)
    finally:
        receiver.close()
        process.join(timeout=5)
        if process.is_alive():
            stop_tree(process)
            process.join(timeout=5)
    print(f"ready_seconds={ready_seconds} terminal={terminal} exitcode={process.exitcode}", flush=True)
    if ready_seconds is None or (args.stop_after_ready and terminal != "cancelled"):
        raise SystemExit(1)
    if not args.stop_after_ready and terminal != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
