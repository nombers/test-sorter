"""
Константы для протокола взаимодействия с роботом через регистры.

============================================================
ПРОТОКОЛ ВЗАИМОДЕЙСТВИЯ PYTHON <-> РОБОТ
============================================================

Number Registers (R[]):
-----------------------
R[1: Iteration_starter] - Состояние итерации (управление потоком)
    0 = READY     - Робот готов принять новую итерацию
    1 = STARTED   - Итерация запущена (Python устанавливает)
    2 = COMPLETED - Итерация завершена (Робот устанавливает)

    ВАЖНО: Python должен сбросить R[1] = 0 после получения R[1] = 2!

R[2: Scan_status] - Статус сканирования
    0 = RESET     - Сброс / Сканирование завершено (Python ставит после скана)
    1 = READY     - Робот в позиции, готов к сканированию (Робот ставит)

    FLOW для сканирования:
    1. Python: R[1] = 1 (запуск)
    2. Робот: едет в позицию, R[2] = 1 (готов)
    3. Python: видит R[2] = 1, сканирует, R[2] = 0 (завершено)
    4. Робот: видит R[2] = 0, R[1] = 2 (итерация завершена)

R[4: Pause_status] - Статус паузы
    0 = NOT_READY - Пауза активна, робот ждёт
    1 = READY     - Сигнал продолжения (Python -> Robot)

    FLOW для паузы:
    1. Python: R[4] = 0, R[1] = 1, SR[1] = "PAUSE"
    2. Робот: едет в home, WAIT R[4] = 1
    3. Python: ждёт действий оператора
    4. Python: R[4] = 1 (продолжить)
    5. Робот: выходит из WAIT, R[1] = 2, R[4] = 0, R[1] = 0

String Registers (SR[]):
------------------------
SR[1: ITERATION_TYPE] - Тип текущей итерации
    "SCANNING_ITERATION" - Сканирование позиции
    "SORTING_ITERATION"  - Сортировка пробирки
    "PAUSE"              - Пауза (робот едет в home)
    "NONE"               - Нет активной итерации

SR[2: MOVEMENT_DATA] - Данные для сортировки
    Формат: "SS TT DD RR"
    SS = Source Pallet (2 цифры, 01-02)
    TT = Source Position (2 цифры, 00-49)
    DD = Destination Rack (2 цифры, 03-09)
    RR = Destination Position (2 цифры, 00-49)

    Пример: "01 23 05 07" = Паллет 1, позиция 23 -> Штатив 5, позиция 7

SR[3: SCAN_DATA] - Данные для сканирования
    Формат: "PP NN"
    PP = Pallet Number (2 цифры, 01-02)
    NN = Position Number (2 цифры, 00-49)

    Пример: "02 35" = Паллет 2, позиция 35 (ряд 7, колонка 0)

============================================================
FLOW ДИАГРАММЫ
============================================================

SCANNING_ITERATION:
    Python                          Robot
    ------                          -----
    wait R[1] = 0
    SR[3] = "PP NN"
    SR[1] = "SCANNING_ITERATION"
    R[1] = 1 ─────────────────────> WAIT R[1] = 1
                                    parse SR[3]
                                    move to position
                                    R[2] = 1 ◄─────────────────┐
    wait R[2] = 1 <─────────────────────────────────────────────┘
    scan barcode
    R[2] = 0 ─────────────────────> WAIT R[2] = 0
                                    R[1] = 2 ◄─────────────────┐
    wait R[1] = 2 <─────────────────────────────────────────────┘
    R[1] = 0 (Python сбрасывает)
    [next iteration]

SORTING_ITERATION:
    Python                          Robot
    ------                          -----
    wait R[1] = 0
    SR[2] = "SS TT DD RR"
    SR[1] = "SORTING_ITERATION"
    R[1] = 1 ─────────────────────> WAIT R[1] = 1
                                    parse SR[2]
                                    pick tube from source
                                    place tube to dest
                                    R[1] = 2 ◄─────────────────┐
    wait R[1] = 2 <─────────────────────────────────────────────┘
    R[1] = 0 (Python сбрасывает)
    [next iteration]

PAUSE:
    Python                          Robot
    ------                          -----
    wait R[1] = 0
    R[4] = 0
    SR[1] = "PAUSE"
    R[1] = 1 ─────────────────────> WAIT R[1] = 1
                                    move to home
                                    WAIT R[4] = 1 (робот ждёт сигнала)
    [wait for operator]
    R[4] = 1 ─────────────────────> (робот видит R[4] = 1)
                                    R[1] = 2 ◄─────────────────┐
    wait R[1] = 2 <─────────────────────────────────────────────┘
    R[1] = 0 (Python сбрасывает)
    [next iteration]

============================================================
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RobotNRNumbers:
    """Номера Number Registers (R[]) для взаимодействия с контроллером робота.

    Каждый атрибут соответствует конкретному регистру на контроллере.
    НЕ ИЗМЕНЯТЬ без согласования с программой робота!

    Attributes:
        iteration_starter: R[1] - состояние итерации (0=готов/1=запущена/2=завершена).
        scan_status: R[2] - статус сканирования (0=сброс/1=готов).
        pause_status: R[4] - статус паузы (0=ждёт/1=продолжить).
        pallet_number: R[10] - номер паллета (только отладка).
        source_position: R[11] - позиция источника (только отладка).
        dest_rack: R[12] - целевой штатив (только отладка).
        dest_position: R[13] - целевая позиция (только отладка).
        current_row: R[14] - текущий ряд (только отладка).
        current_col: R[15] - текущая колонка (только отладка).
    """
    # Основные регистры управления
    iteration_starter: int = 1   # R[1] - состояние итерации (0/1/2)
    scan_status: int = 2         # R[2] - статус сканирования (0/1)
    pause_status: int = 4        # R[4] - статус паузы (0/1)

    # Информационные регистры (только для отладки, не используются в протоколе)
    pallet_number: int = 10      # R[10] - номер паллета
    source_position: int = 11    # R[11] - позиция источника
    dest_rack: int = 12          # R[12] - целевой штатив
    dest_position: int = 13      # R[13] - целевая позиция
    current_row: int = 14        # R[14] - текущий ряд
    current_col: int = 15        # R[15] - текущая колонка


@dataclass(frozen=True)
class RobotNRValues:
    """Значения для Number Registers.

    Определяет допустимые значения, которые записываются/читаются из R[].
    НЕ ИЗМЕНЯТЬ без согласования с программой робота!

    Attributes:
        ready: R[1]=0 - робот готов принять новую итерацию.
        started: R[1]=1 - итерация запущена (Python -> Robot).
        completed: R[1]=2 - итерация завершена (Robot -> Python).
        scan_reset: R[2]=0 - сканирование завершено (Python -> Robot).
        scan_good: R[2]=1 - робот в позиции, готов к сканированию (Robot -> Python).
        scan_bad: R[2]=2 - не используется в текущем протоколе.
        pause_not_ready: R[4]=0 - пауза активна, робот ждёт.
        pause_ready: R[4]=1 - сигнал продолжения (Python -> Robot).
    """
    # R[1: iteration_starter] values
    ready: int = 0           # Робот готов принять новую итерацию
    started: int = 1         # Итерация запущена (Python -> Robot)
    completed: int = 2       # Итерация завершена (Robot -> Python)

    # R[2: scan_status] values
    scan_reset: int = 0      # Сброс / сканирование завершено (Python -> Robot)
    scan_good: int = 1       # Робот в позиции, готов к сканированию (Robot -> Python)
    scan_bad: int = 2        # Не используется в новом протоколе

    # R[4: pause_status] values
    pause_not_ready: int = 0 # Пауза активна, робот ждёт
    pause_ready: int = 1     # Сигнал продолжения (Python -> Robot)


@dataclass(frozen=True)
class RobotSRNumbers:
    """Номера String Registers (SR[]) для взаимодействия с контроллером робота.

    Каждый атрибут соответствует конкретному строковому регистру на контроллере.
    НЕ ИЗМЕНЯТЬ без согласования с программой робота!

    Attributes:
        iteration_type: SR[1] - тип текущей итерации (строковое значение).
        movement_data: SR[2] - данные для сортировки в формате "SS TT DD RR".
        scan_data: SR[3] - данные для сканирования в формате "PP NN".
    """
    iteration_type: int = 1  # SR[1] - тип итерации (строка)
    movement_data: int = 2   # SR[2] - данные для сортировки "SS TT DD RR"
    scan_data: int = 3       # SR[3] - данные для сканирования "PP NN"


@dataclass(frozen=True)
class RobotSRValues:
    """Значения для String Registers (SR[1: ITERATION_TYPE]).

    Строковые константы, записываемые в SR[1] для указания типа итерации.
    НЕ ИЗМЕНЯТЬ без согласования с программой робота!

    Attributes:
        sorting: Команда сортировки пробирок.
        scanning: Команда сканирования позиций.
        pause: Команда паузы (робот едет в home).
        none: Нет активной итерации.
    """
    sorting: str = "SORTING_ITERATION"    # Сортировка пробирок
    scanning: str = "SCANNING_ITERATION"  # Сканирование позиций
    pause: str = "PAUSE"                  # Пауза (робот в home)
    none: str = "NONE"                    # Нет активной итерации


# ============================================================
# Глобальные экземпляры для удобного использования
# ============================================================
#
# Использование:
#   from .robot_protocol import NR, NR_VAL, SR, SR_VAL
#
#   robot.set_number_register(NR.iteration_starter, NR_VAL.started)
#   robot.set_string_register(SR.iteration_type, SR_VAL.scanning)
#
#   if robot.get_number_register(NR.scan_status) == NR_VAL.scan_good:
#       # робот готов к сканированию
#

NR = RobotNRNumbers()
NR_VAL = RobotNRValues()
SR = RobotSRNumbers()
SR_VAL = RobotSRValues()
