"""Точка входа для запуска Eppendorf Sorter в режиме без GUI (headless)."""

from src.eppendorf_sorter.orchestration.bootstrap import run_workcell

if __name__ == "__main__":
    run_workcell()