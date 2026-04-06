"""Пакет логирования для eppendorf_sorter.

Предоставляет фабрику логгеров с автоматической организацией лог-файлов
по датам и глобальные хуки для перехвата необработанных исключений.
"""

from .logger_factory import create_logger
from .custom_hooks import install_global_exception_hooks

__all__ = [
    "create_logger",
    "install_global_exception_hooks",
]
