# src/eppendorf_sorter/logging/exception_hooks.py
import sys
import threading
import logging
from types import TracebackType
from typing import Type


BANNER_START = "START OF EXCEPT MESSAGE"
BANNER_END = "END OF EXCEPT MESSAGE"


def _format_trace_path(tb: TracebackType) -> str:
    names = []
    cur = tb
    while cur:
        names.append(cur.tb_frame.f_code.co_name)
        cur = cur.tb_next
    # Срез [2:] чтобы не засорять служебными фреймами
    return " -> ".join(names[2:])


def install_global_exception_hooks(logger: logging.Logger | None = None) -> None:
    """
    Устанавливает кастомные хуки для:
    - необработанных исключений в главном потоке (sys.excepthook)
    - исключений в потоках (threading.excepthook)
    Если logger передан — пишет в лог, иначе печатает в stdout.
    """

    def sys_hook(exc_type: Type[BaseException],
                 exc_value: BaseException,
                 exc_traceback: TracebackType | None) -> None:
        if exc_traceback is None:
            path = "<no traceback>"
        else:
            path = _format_trace_path(exc_traceback)

        msg = (
            f"{BANNER_START:-^70}\n"
            f"Thread: {threading.current_thread().name}\n"
            f"Traceback:\n{path}\n"
            f"Exception:\n{getattr(exc_type, '__name__', str(exc_type))}: {exc_value}\n"
            f"{BANNER_END:-^70}"
        )

        if logger:
            logger.critical(msg)
        else:
            print(msg, file=sys.stderr)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        tb = args.exc_traceback
        if tb is None:
            path = "<no traceback>"
        else:
            path = _format_trace_path(tb)

        msg = (
            f"{BANNER_START:-^70}\n"
            f"Thread: {args.thread.name}\n"
            f"Traceback:\n{path}\n"
            f"Exception:\n{args.exc_type.__name__}: {args.exc_value}\n"
            f"{BANNER_END:-^70}"
        )

        if logger:
            logger.critical(msg)
        else:
            print(msg, file=sys.stderr)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
