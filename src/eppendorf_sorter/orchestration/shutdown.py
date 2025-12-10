# src/mindray_automation_2/orchestration/shutdown.py
import threading
import logging

def shutdown(stop_event: threading.Event, threads: list[threading.Thread], logger:logging.Logger):
    logger.info("Остановка системы...")
    stop_event.set()
    
    for th in threads:
        if th.is_alive():
            th.join(timeout=5)

    logger.info("Остановка завершена")