# logging/logger_factory.py
import logging
import os
from datetime import datetime

def create_logger(
    name: str, 
    log_filename: str, 
    base_log_path: str = "logs",
    console_output: bool = True  
) -> logging.Logger:
    """Создать логгер с автоматической структурой папок по дате внутри проекта"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Очищаем существующие обработчики
    logger.handlers.clear()
    
    # Создаем структуру папок: logs/15.01.2024/
    current_date = datetime.now()
    date_folder = current_date.strftime("%d.%m.%Y")  # Формат: день.месяц.год
    date_path = os.path.join(base_log_path, date_folder)
    
    os.makedirs(date_path, exist_ok=True)
    full_log_path = os.path.join(date_path, log_filename)
    
    # Файловый обработчик 
    file_handler = logging.FileHandler(full_log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    ))
    logger.addHandler(file_handler)
    
    # Консольный обработчик (только если включен)
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(console_handler)
    
    return logger