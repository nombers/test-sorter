"""
Константы для протокола взаимодействия с роботом через регистры.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RobotNRNumbers:
    """Номера Number Registers для movement_robot."""
    iteration_starter: int = 1  # команда старт/стоп итерации
    grip_status: int = 2         # статус захвата
    scan_status: int = 3         # статус сканирования QR
    scan_delay: int = 4          # задержка для ориентации по QR
    move_status: int = 5         # статус движения


@dataclass(frozen=True)
class RobotNRValues:
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
class RobotSRNumbers:
    """Номера String Registers для movement_robot."""
    iteration_type: int = 1  # тип итерации
    movement_data: int = 2    # данные для движения пробирки
    scan_data: int = 3        # данные для сканирования


@dataclass(frozen=True)
class RobotSRValues:
    """Значения для String Registers."""
    sorting: str = "SORTING_ITERATION"    # сортировка пробирок
    scanning: str = "SCANNING_ITERATION"  # сканирование штативов
    waiting: str = "WAITING"              # ожидание замены штативов
    none: str = "NONE"


# Глобальные экземпляры для удобного использования
NR = RobotNRNumbers()
NR_VAL = RobotNRValues()
SR = RobotSRNumbers()
SR_VAL = RobotSRValues()