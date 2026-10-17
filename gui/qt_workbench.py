"""Compatibility entry point for the modular Qt workbench.

The public module path remains stable for ``main.py`` and existing packaging
configuration. The window shell, pages, canvas and UI primitives now live in
focused modules under :mod:`gui`.
"""
from .workbench import Workbench, main

__all__ = ("Workbench", "main")
