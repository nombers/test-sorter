# src/eppendorf_sorter/logging/exception_hooks.py
"""
Глобальные хуки для перехвата необработанных исключений.

Заменяет стандартные sys.excepthook и threading.excepthook на кастомные,
которые форматируют трейсбек в читаемый вид и направляют его в логгер
или stderr.
"""

import sys
import threading
import logging
from types import TracebackType
from typing import Type


BANNER_START = "START OF EXCEPT MESSAGE"
BANNER_END = "END OF EXCEPT MESSAGE"


def _format_trace_path(tb: TracebackType) -> str:
    """Формирует строку с цепочкой вызовов из объекта трейсбека.

    Обходит стек фреймов и собирает имена функций в порядке вызова.
    Первые два фрейма — служебные (хук + диспетчер), поэтому обрезаются.

    Args:
        tb: Объект трейсбека исключения.

    Returns:
        Строка вида "func_a -> func_b -> func_c".
    """
    names = []
    cur = tb
    while cur:
        names.append(cur.tb_frame.f_code.co_name)
        cur = cur.tb_next
    # Срез [2:] чтобы не засорять служебными фреймами хука и диспетчера
    return " -> ".join(names[2:])


def install_global_exception_hooks(logger: logging.Logger | None = None) -> None:
    """Устанавливает кастомные хуки для перехвата необработанных исключений.

    Перехватывает исключения в главном потоке (через sys.excepthook)
    и во вторичных потоках (через threading.excepthook), форматирует
    их в единый стиль с баннером и путём вызовов.

    Args:
        logger: Логгер для записи сообщений об исключениях. Если None —
            сообщения выводятся в stderr.
    """

    def sys_hook(exc_type: Type[BaseException],
                 exc_value: BaseException,
                 exc_traceback: TracebackType | None) -> None:
        """Хук для необработанных исключений в главном потоке."""
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
        """Хук для необработанных исключений во вторичных потоках."""
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
