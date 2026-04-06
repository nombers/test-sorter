# logging/logger_factory.py
"""Фабрика логгеров с автоматической организацией файлов по датам."""

import logging
import os
from datetime import datetime


def create_logger(
    name: str,
    log_filename: str,
    base_log_path: str = "logs",
    console_output: bool = True
) -> logging.Logger:
    """Создаёт логгер с файловым и опциональным консольным обработчиком.

    Файлы логов организуются в подпапки по текущей дате:
    ``<base_log_path>/<DD.MM.YYYY>/<log_filename>``.

    Args:
        name: Имя логгера (обычно ``__name__`` вызывающего модуля).
        log_filename: Имя файла лога (например, ``"robot.log"``).
        base_log_path: Корневая директория для хранения логов.
        console_output: Если True — дублирует вывод в консоль.

    Returns:
        Настроенный экземпляр :class:`logging.Logger`.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Сбрасываем обработчики, чтобы избежать дублирования при повторном вызове
    logger.handlers.clear()

    # --- Построение пути к файлу лога ---
    current_date = datetime.now()
    date_folder = current_date.strftime("%d.%m.%Y")  # Формат: день.месяц.год
    date_path = os.path.join(base_log_path, date_folder)

    os.makedirs(date_path, exist_ok=True)
    full_log_path = os.path.join(date_path, log_filename)

    # --- Файловый обработчик ---
    file_handler = logging.FileHandler(full_log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    ))
    logger.addHandler(file_handler)

    # --- Консольный обработчик (только если включён) ---
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(console_handler)

    return logger
