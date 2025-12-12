"""
Константы для протокола взаимодействия с роботом через регистры.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class LoaderNRNumbers:
    """Номера Number Registers для loader_robot."""
    iteration_starter: int = 1  # команда старт/стоп итерации
    grip_status: int = 2         # статус захвата
    scan_status: int = 3         # статус сканирования
    scan_delay: int = 4          # задержка сканирования
    move_status: int = 5         # статус движения


@dataclass(frozen=True)
class LoaderNRValues:
    """Значения для Number Registers."""
    # iteration_starter values
    start: int = 1
    reset: int = 0
    end: int = 2
    
    # grip_status values
    grip_good: int = 1
    grip_bad: int = 2
    grip_reset: int = 0
    
    # scan_status values
    scan_good: int = 1
    scan_bad: int = 2
    scan_reset: int = 0
    
    # scan_delay values
    delay_reset: float = 0.0
    
    # move_status values
    move_start: int = 1
    move_stop: int = 0


@dataclass(frozen=True)
class LoaderSRNumbers:
    """Номера String Registers для loader_robot."""
    iteration_type: int = 1  # тип итерации
    loading_data: int = 2    # данные для движения


@dataclass(frozen=True)
class LoaderSRValues:
    """Значения для String Registers."""
    loading: str = "LOADING_ITERATION"
    stacking: str = "STACKING_ITERATION"
    breaking: str = "BREAK_ITERATION"
    none: str = "NONE"


# Глобальные экземпляры для удобного использования
NR = LoaderNRNumbers()
NR_VAL = LoaderNRValues()
SR = LoaderSRNumbers()
SR_VAL = LoaderSRValues()
