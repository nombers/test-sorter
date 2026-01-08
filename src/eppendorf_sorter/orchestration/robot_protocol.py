"""
Константы для протокола взаимодействия с роботом через регистры.

ПРОТОКОЛ ВЗАИМОДЕЙСТВИЯ:
========================

Number Registers (R[]):
-----------------------
R[1: Iteration_starter]  - флаг готовности к новой итерации (0 = готов принять, 1 = запущена итерация, 2 = завершена)
R[2: Scan_status]        - статус сканирования (0 = сброс, 1 = хороший скан, 2 = плохой скан)
R[4: Pause_status]       - статус паузы (0 = не готов, 1 = пауза завершена, продолжаем)

String Registers (SR[]):
------------------------
SR[1: ITERATION_TYPE]    - тип итерации (строка: "SCANNING_ITERATION" / "SORTING_ITERATION" / "PAUSE" / "NONE")
SR[2: MOVEMENT_DATA]     - данные для сортировки "SS TT DD RR" (source_pallet source_pos dest_rack dest_pos)
SR[3: SCAN_DATA]         - данные для сканирования "PP NN" (pallet_id position)

FLOW:
=====
1. Python: ждёт R[1: Iteration_starter] = 0
2. Python: устанавливает SR[2] или SR[3] с данными
3. Python: устанавливает SR[1: ITERATION_TYPE] = "SCANNING_ITERATION" или "SORTING_ITERATION"
4. Робот: видит смену типа итерации, выполняет, устанавливает R[1: Iteration_starter] = 2
5. Python: видит R[1: Iteration_starter] = 2, сбрасывает в 0
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RobotNRNumbers:
    """Номера Number Registers для movement_robot."""
    iteration_starter: int = 1       # R[1: Iteration_starter] - состояние итерации
    scan_status: int = 2             # R[2] - статус сканирования
    pause_status: int = 4            # R[4] - статус паузы

    # Дополнительные регистры (для информации, но не используются в протоколе)
    pallet_number: int = 10
    source_position: int = 11
    dest_rack: int = 12
    dest_position: int = 13


@dataclass(frozen=True)
class RobotNRValues:
    """Значения для Number Registers."""
    # iteration_starter values
    ready: int = 0           # Робот готов принять новую итерацию
    started: int = 1         # Итерация запущена (робот работает)
    completed: int = 2       # Итерация завершена

    # scan_status values
    scan_reset: int = 0      # Сброс (ожидание)
    scan_good: int = 1       # Успешное сканирование
    scan_bad: int = 2        # Плохое сканирование (нет баркода)

    # pause_status values
    pause_not_ready: int = 0 # Пауза не завершена
    pause_ready: int = 1     # Готов продолжить после паузы


@dataclass(frozen=True)
class RobotSRNumbers:
    """Номера String Registers для movement_robot."""
    iteration_type: int = 1  # SR[1: ITERATION_TYPE] - строковый тип итерации
    movement_data: int = 2   # SR[2] - данные для движения "SS TT DD RR"
    scan_data: int = 3       # SR[3] - данные для сканирования "PP NN"


@dataclass(frozen=True)
class RobotSRValues:
    """Значения для String Registers (SR[1: ITERATION_TYPE])."""
    sorting: str = "SORTING_ITERATION"    # Сортировка пробирок
    scanning: str = "SCANNING_ITERATION"  # Сканирование штативов
    pause: str = "PAUSE"                  # Пауза (робот в home позиции)
    none: str = "NONE"                    # Нет активной итерации


# Глобальные экземпляры для удобного использования
NR = RobotNRNumbers()
NR_VAL = RobotNRValues()
SR = RobotSRNumbers()
SR_VAL = RobotSRValues()