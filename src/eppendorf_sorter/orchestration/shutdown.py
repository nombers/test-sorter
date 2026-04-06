"""Модуль корректного завершения работы системы.

Предоставляет функцию для последовательной остановки всех рабочих потоков
через глобальное событие остановки с ожиданием их завершения.
"""
# src/mindray_automation_2/orchestration/shutdown.py
import threading
import logging


def shutdown(stop_event: threading.Event, threads: list[threading.Thread], logger: logging.Logger):
    """Корректно останавливает все рабочие потоки системы.

    Устанавливает глобальное событие остановки и последовательно
    ожидает завершения каждого потока с таймаутом.

    Args:
        stop_event: Глобальное событие, сигнализирующее потокам о необходимости
            завершения.
        threads: Список потоков, которые необходимо остановить.
        logger: Логгер для записи информации о процессе остановки.
    """
    logger.info("Остановка системы...")
    stop_event.set()

    for th in threads:
        if th.is_alive():
            th.join(timeout=5)

    logger.info("Остановка завершена")
