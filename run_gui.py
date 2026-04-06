#!/usr/bin/env python3
"""
Точка входа для запуска GUI приложения Eppendorf Sorter.

Использование:
    python run_gui.py
"""

from src.eppendorf_sorter.orchestration.bootstrap_gui import run_gui

if __name__ == "__main__":
    run_gui()
