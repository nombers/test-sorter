"""Модуль управления штативами и паллетами для системы сортировки пробирок.

Содержит доменные модели для исходных (SourceRack) и целевых (DestinationRack)
штативов, информацию о пробирках (TubeInfo) и менеджер системы (RackSystemManager).
Все операции с состоянием являются потокобезопасными (thread-safe).

Интегрируется с main.py и обеспечивает thread-safe управление.
"""

from enum import Enum
from typing import Dict, Optional, Tuple, List
import threading
import logging

logger = logging.getLogger(__name__)


# ===================== ENUMS =====================

class TestType(Enum):
    """Типы лабораторных тестов, назначаемых пробиркам.

    Каждое значение соответствует идентификатору протокола ПЦР-анализа,
    используемому при взаимодействии с ЛИС (лабораторной информационной
    системой).

    Attributes:
        UGI: Тест на урогенитальные инфекции (pcr-1).
        VPCH: Тест на вирус папилломы человека (pcr-2).
        UGI_VPCH: Комбинированный тест UGI + VPCH (pcr-1+pcr-2).
        OTHER: Прочие ПЦР-тесты, не относящиеся к основным категориям.
        ERROR: Маркер ошибки при определении типа теста.
        UNKNOWN: Тип теста не удалось определить.
    """

    UGI = "pcr-1"
    VPCH = "pcr-2"
    UGI_VPCH = "pcr-1+pcr-2"
    OTHER = "pcr"
    ERROR = "error"
    UNKNOWN = "unknown"


class RackStatus(Enum):
    """Статусы заполненности штатива пробирками.

    Attributes:
        EMPTY: Штатив пуст (нет пробирок).
        PARTIAL: Штатив частично заполнен.
        TARGET_REACHED: Достигнуто целевое количество пробирок.
        FULL: Штатив полностью заполнен (50/50).
    """

    EMPTY = "empty"
    PARTIAL = "partial"
    TARGET_REACHED = "target_reached"
    FULL = "full"


class RackOccupancy(Enum):
    """Статусы занятости штатива роботом или оператором.

    Attributes:
        FREE: Штатив свободен для операций.
        BUSY_ROBOT: Штатив занят роботом (идёт перемещение пробирок).
        WAITING_REPLACE: Штатив ожидает замены оператором.
    """

    FREE = "free"
    BUSY_ROBOT = "busy_robot"
    WAITING_REPLACE = "waiting_replace"


# ===================== TUBEINFO =====================

class TubeInfo:
    """Информация о пробирке в системе сортировки.

    Хранит данные об источнике пробирки (исходный штатив и позиция),
    назначенном типе теста и фактическом размещении в целевом штативе.

    Штатив содержит сетку 10 рядов x 5 колонок = 50 позиций.
    Номер позиции (0-49) вычисляется как row * 5 + col.

    Attributes:
        barcode: Уникальный штрихкод пробирки.
        source_rack: ID исходного штатива, из которого взята пробирка.
        number: Порядковый номер позиции в исходном штативе (0-49).
        test_type: Тип назначенного лабораторного теста.
        raw_tests: Список сырых (необработанных) тестов из ответа ЛИС.
        destination_rack: ID целевого штатива, куда помещена пробирка
            (None, если ещё не размещена).
        destination_number: Позиция в целевом штативе (0-49)
            (None, если ещё не размещена).
    """

    def __init__(self, barcode: str, source_rack: int, number: int,
                 test_type: TestType):
        """Инициализирует информацию о пробирке.

        Args:
            barcode: Уникальный штрихкод пробирки.
            source_rack: ID исходного штатива.
            number: Порядковый номер позиции в исходном штативе (0-49).
            test_type: Тип назначенного лабораторного теста.
        """
        self.barcode = barcode
        self.source_rack = source_rack
        self.number = number
        self.test_type = test_type
        self.raw_tests: List[str] = []

        # Фактическое размещение (куда поместили пробирку)
        self.destination_rack: Optional[int] = None
        self.destination_number: Optional[int] = None

    # ========== СВОЙСТВА ДЛЯ ИСТОЧНИКА ==========

    @property
    def row(self) -> int:
        """Вычисляет ряд в исходном штативе из номера позиции.

        Returns:
            Номер ряда (0-9).
        """
        return self.number // 5

    @property
    def col(self) -> int:
        """Вычисляет колонку в исходном штативе из номера позиции.

        Returns:
            Номер колонки (0-4).
        """
        return self.number % 5

    # ========== СВОЙСТВА ДЛЯ ФАКТИЧЕСКОГО РАЗМЕЩЕНИЯ ==========

    @property
    def destination_row(self) -> Optional[int]:
        """Вычисляет ряд в целевом штативе из номера позиции назначения.

        Returns:
            Номер ряда (0-9) или None, если пробирка не размещена.
        """
        if self.destination_number is None:
            return None
        return self.destination_number // 5

    @property
    def destination_col(self) -> Optional[int]:
        """Вычисляет колонку в целевом штативе из номера позиции назначения.

        Returns:
            Номер колонки (0-4) или None, если пробирка не размещена.
        """
        if self.destination_number is None:
            return None
        return self.destination_number % 5

    # ========== ПРОВЕРКИ ==========

    @property
    def is_placed(self) -> bool:
        """Проверяет, была ли пробирка физически размещена в целевой штатив.

        Returns:
            True, если пробирка размещена в целевом штативе.
        """
        return self.destination_rack is not None

    # ========== УТИЛИТЫ ==========

    @staticmethod
    def number_from_position(row: int, col: int) -> int:
        """Преобразует координаты (ряд, колонка) в линейный номер позиции.

        Args:
            row: Номер ряда (0-9).
            col: Номер колонки (0-4).

        Returns:
            Линейный номер позиции (0-49).
        """
        return row * 5 + col

    @staticmethod
    def position_from_number(number: int) -> Tuple[int, int]:
        """Преобразует линейный номер позиции в координаты (ряд, колонка).

        Args:
            number: Линейный номер позиции (0-49).

        Returns:
            Кортеж (row, col) с координатами позиции.
        """
        return (number // 5, number % 5)

    def __repr__(self):
        placement = ""
        if self.is_placed:
            placement = f", dest=R{self.destination_rack}[#{self.destination_number}]"

        return (f"TubeInfo(barcode={self.barcode}, "
                f"source=P{self.source_rack}[#{self.number}:r{self.row}c{self.col}], "
                f"type={self.test_type.value}"
                f"{placement})")



# ===================== BASE RACK CLASS =====================

class BaseRack:
    """Базовый класс для штативов системы сортировки.

    Содержит общую логику управления пробирками, статусами занятости
    и потокобезопасного доступа к данным. Является родительским классом
    для SourceRack и DestinationRack.

    Attributes:
        MAX_TUBES: Максимальная вместимость штатива (50 пробирок).
        rack_id: Уникальный идентификатор штатива.
        tubes: Список пробирок, находящихся в штативе.
    """

    MAX_TUBES = 50

    def __init__(self, rack_id: int):
        """Инициализирует базовый штатив.

        Args:
            rack_id: Уникальный идентификатор штатива.
        """
        self.rack_id = rack_id
        self.tubes: List[TubeInfo] = []
        self._occupancy = RackOccupancy.FREE
        self._lock = threading.Lock()

    # ---------------------- ЗАНЯТОСТЬ ----------------------

    def get_occupancy(self) -> RackOccupancy:
        """Возвращает текущий статус занятости штатива.

        Returns:
            Текущий статус занятости.
        """
        with self._lock:
            return self._occupancy

    def set_occupancy(self, occupancy: RackOccupancy):
        """Устанавливает статус занятости штатива.

        Args:
            occupancy: Новый статус занятости.

        Raises:
            ValueError: Если передано значение, не являющееся RackOccupancy.
        """
        with self._lock:
            if not isinstance(occupancy, RackOccupancy):
                raise ValueError("Статус занятости должен соответствовать RackOccupancy")
            self._occupancy = occupancy

    def occupy(self):
        """Помечает штатив как занятый роботом."""
        self.set_occupancy(RackOccupancy.BUSY_ROBOT)

    def release(self):
        """Освобождает штатив (устанавливает статус FREE)."""
        self.set_occupancy(RackOccupancy.FREE)

    def mark_waiting_replace(self):
        """Помечает штатив как ожидающий замены оператором."""
        self.set_occupancy(RackOccupancy.WAITING_REPLACE)

    def is_available(self) -> bool:
        """Проверяет, доступен ли штатив для операций.

        Returns:
            True, если штатив свободен (статус FREE).
        """
        with self._lock:
            return self._occupancy == RackOccupancy.FREE

    def is_busy(self) -> bool:
        """Проверяет, занят ли штатив.

        Returns:
            True, если штатив не свободен (статус отличен от FREE).
        """
        with self._lock:
            return self._occupancy != RackOccupancy.FREE

    # ---------------------- РАБОТА С ПРОБИРКАМИ ----------------------

    def get_tube_count(self) -> int:
        """Возвращает количество пробирок в штативе.

        Returns:
            Количество пробирок.
        """
        with self._lock:
            return len(self.tubes)

    def get_barcodes(self) -> List[str]:
        """Возвращает список штрихкодов всех пробирок в штативе.

        Returns:
            Список строк-штрихкодов.
        """
        with self._lock:
            return [tube.barcode for tube in self.tubes]

    def get_tubes(self) -> List[TubeInfo]:
        """Возвращает копию списка пробирок в штативе.

        Returns:
            Копия списка объектов TubeInfo.
        """
        with self._lock:
            return self.tubes.copy()

    def get_tube_by_barcode(self, barcode: str) -> Optional[TubeInfo]:
        """Находит пробирку по штрихкоду.

        Args:
            barcode: Штрихкод для поиска.

        Returns:
            Объект TubeInfo или None, если пробирка не найдена.
        """
        with self._lock:
            return self._get_tube_by_barcode_unsafe(barcode)

    def _get_tube_by_barcode_unsafe(self, barcode: str) -> Optional[TubeInfo]:
        """Находит пробирку по штрихкоду без блокировки.

        Предназначен для внутреннего использования в методах, которые
        уже захватили блокировку ``_lock``.

        Args:
            barcode: Штрихкод для поиска.

        Returns:
            Объект TubeInfo или None, если пробирка не найдена.
        """
        for tube in self.tubes:
            if tube.barcode == barcode:
                return tube
        return None

    def _has_barcode_unsafe(self, barcode: str) -> bool:
        """Проверяет наличие пробирки без блокировки.

        Args:
            barcode: Штрихкод для проверки.

        Returns:
            True, если пробирка с данным штрихкодом присутствует.
        """
        return self._get_tube_by_barcode_unsafe(barcode) is not None

    def has_barcode(self, barcode: str) -> bool:
        """Проверяет наличие пробирки с указанным штрихкодом.

        Args:
            barcode: Штрихкод для проверки.

        Returns:
            True, если пробирка с данным штрихкодом присутствует.
        """
        with self._lock:
            return self._has_barcode_unsafe(barcode)

    # ---------------------- СТАТУСЫ ----------------------

    def is_empty(self) -> bool:
        """Проверяет, пуст ли штатив.

        Returns:
            True, если в штативе нет пробирок.
        """
        with self._lock:
            return len(self.tubes) == 0

    def is_full(self) -> bool:
        """Проверяет, полностью ли заполнен штатив.

        Returns:
            True, если количество пробирок достигло MAX_TUBES.
        """
        with self._lock:
            return len(self.tubes) >= self.MAX_TUBES

    # ---------------------- СБРОС ----------------------

    def reset(self):
        """Сбрасывает штатив в начальное состояние.

        Очищает список пробирок и устанавливает статус занятости FREE.
        Используется при физической замене штатива.
        """
        with self._lock:
            self.tubes = []
            self._occupancy = RackOccupancy.FREE
            logger.info(f"Rack #{self.rack_id} сброшен")


# ===================== SOURCE Rack =====================

class SourceRack(BaseRack):
    """Исходный штатив (паллет) с пробирками для сканирования и сортировки.

    Расширяет BaseRack функциональностью сканирования пробирок,
    отслеживания прогресса сортировки и статистики по типам тестов.

    Attributes:
        MAX_TUBES: Максимальная вместимость штатива (50 пробирок).
        rack_id: Уникальный идентификатор паллета.
        tubes: Список отсканированных пробирок.
    """

    def __init__(self, pallet_id: int):
        """Инициализирует исходный штатив (паллет).

        Args:
            pallet_id: Уникальный ID паллета (0, 1, 2, ...).
        """
        super().__init__(pallet_id)
        self._sorted_count = 0

    @property
    def pallet_id(self) -> int:
        """Алиас для rack_id для обратной совместимости.

        Returns:
            ID паллета.
        """
        return self.rack_id

    # ---------------------- ОПЕРАЦИИ С ПРОБИРКАМИ ----------------------

    def add_scanned_tube(self, tube: TubeInfo):
        """Добавляет отсканированную пробирку в паллет.

        Проверяет принадлежность пробирки данному паллету и отсутствие
        дубликатов. Дубликаты игнорируются с предупреждением в лог.

        Args:
            tube: Информация о пробирке для добавления.

        Raises:
            ValueError: Если пробирка принадлежит другому паллету.
        """
        with self._lock:
            if tube.source_rack != self.rack_id:
                raise ValueError(f"Пробирка принадлежит паллету {tube.source_rack}, а не {self.rack_id}")

            if self._has_barcode_unsafe(tube.barcode):
                logger.warning(f"Пробирка {tube.barcode} уже в паллете П{self.rack_id}")
                return

            self.tubes.append(tube)
            logger.debug(f"Пробирка {tube.barcode} добавлена в П{self.rack_id} ({len(self.tubes)}/50)")

    def add_scanned_tubes(self, tubes: List[TubeInfo]):
        """Добавляет несколько отсканированных пробирок в паллет.

        Args:
            tubes: Список пробирок для добавления.
        """
        for tube in tubes:
            self.add_scanned_tube(tube)

    def mark_tube_sorted(self, barcode: str) -> bool:
        """Отмечает пробирку как отсортированную.

        Args:
            barcode: Штрихкод пробирки.

        Returns:
            True, если пробирка успешно отмечена; False, если пробирка
            не найдена или уже была отмечена ранее.
        """
        with self._lock:
            tube = self._get_tube_by_barcode_unsafe(barcode)
            if not tube:
                logger.warning(f"Пробирка {barcode} не найдена в П{self.rack_id}")
                return False

            if tube.destination_rack is not None:
                logger.warning(f"Пробирка {barcode} уже отмечена как отсортированная")
                return False

            self._sorted_count += 1
            logger.debug(f"Пробирка {barcode} отсортирована из П{self.rack_id} ({self._sorted_count}/{len(self.tubes)})")
            return True

    # ---------------------- СТАТУСЫ И ПРОВЕРКИ ----------------------

    def get_status(self) -> RackStatus:
        """Определяет текущий статус заполненности паллета.

        Returns:
            Статус заполненности. EMPTY возвращается также когда
            все пробирки уже отсортированы.
        """
        with self._lock:
            scanned = len(self.tubes)

            if scanned == 0:
                return RackStatus.EMPTY
            elif self._sorted_count >= scanned:
                return RackStatus.EMPTY  # Все отсортированы
            elif scanned >= self.MAX_TUBES:
                return RackStatus.FULL
            else:
                return RackStatus.PARTIAL


    def get_sorted_count(self) -> int:
        """Возвращает количество отсортированных пробирок.

        Returns:
            Количество пробирок, отмеченных как отсортированные.
        """
        with self._lock:
            return self._sorted_count

    def get_remaining_count(self) -> int:
        """Возвращает количество пробирок, ожидающих сортировки.

        Returns:
            Разница между общим числом пробирок и отсортированными.
        """
        with self._lock:
            return len(self.tubes) - self._sorted_count

    def is_fully_scanned(self) -> bool:
        """Проверяет, все ли позиции штатива отсканированы.

        Returns:
            True, если количество пробирок достигло MAX_TUBES.
        """
        with self._lock:
            return len(self.tubes) >= self.MAX_TUBES

    def has_tubes_to_sort(self) -> bool:
        """Проверяет наличие неотсортированных пробирок.

        Returns:
            True, если есть пробирки, ожидающие сортировки.
        """
        with self._lock:
            return self._sorted_count < len(self.tubes)

    def get_unsorted_tubes(self) -> List[TubeInfo]:
        """Возвращает список неотсортированных пробирок.

        Returns:
            Список пробирок, у которых ещё не назначен целевой штатив.
        """
        with self._lock:
            return [t for t in self.tubes if t.destination_rack is None]

    def get_tubes_by_type(self, test_type: TestType) -> List[TubeInfo]:
        """Возвращает пробирки с указанным типом теста.

        Args:
            test_type: Тип теста для фильтрации.

        Returns:
            Список пробирок с совпадающим типом теста.
        """
        with self._lock:
            return [t for t in self.tubes if t.test_type == test_type]

    # ---------------------- ПРОГРЕСС И СТАТИСТИКА ----------------------

    def get_scan_progress(self) -> float:
        """Вычисляет прогресс сканирования паллета.

        Returns:
            Доля отсканированных позиций (0.0 - 1.0).
        """
        with self._lock:
            if self.MAX_TUBES == 0:
                return 1.0
            return min(len(self.tubes) / self.MAX_TUBES, 1.0)

    def get_sort_progress(self) -> float:
        """Вычисляет прогресс сортировки пробирок паллета.

        Returns:
            Доля отсортированных пробирок (0.0 - 1.0). Возвращает 0.0,
            если в паллете нет пробирок.
        """
        with self._lock:
            if len(self.tubes) == 0:
                return 0.0
            return self._sorted_count / len(self.tubes)

    def get_statistics_by_type(self) -> Dict[TestType, int]:
        """Собирает статистику количества пробирок по типам тестов.

        Returns:
            Словарь {тип_теста: количество_пробирок}.
        """
        with self._lock:
            stats = {}
            for tube in self.tubes:
                stats[tube.test_type] = stats.get(tube.test_type, 0) + 1
            return stats

    # ---------------------- УПРАВЛЕНИЕ ----------------------

    def reset(self):
        """Сбрасывает паллет в начальное состояние.

        Очищает список пробирок, обнуляет счётчик отсортированных
        и устанавливает статус занятости FREE.
        """
        with self._lock:
            self.tubes = []
            self._sorted_count = 0
            self._occupancy = RackOccupancy.FREE
            logger.info(f"Паллет П{self.rack_id} сброшен")

    def clear_sorted(self):
        """Удаляет из памяти пробирки, уже размещённые в целевых штативах.

        Оставляет только неотсортированные пробирки и обнуляет счётчик.
        """
        with self._lock:
            self.tubes = [t for t in self.tubes if t.destination_rack is None]
            self._sorted_count = 0
            logger.debug(f"Очищены отсортированные пробирки из П{self.rack_id}")



# ===================== DESTINATION RACK =====================

class DestinationRack(BaseRack):
    """Целевой штатив для размещения отсортированных пробирок.

    Расширяет BaseRack функциональностью целевых значений, автоматической
    нумерации позиций и контроля заполненности. Каждый целевой штатив
    привязан к определённому типу теста.

    Attributes:
        MAX_TUBES: Максимальная вместимость штатива (50 пробирок).
        rack_id: Уникальный идентификатор штатива.
        tubes: Список размещённых пробирок.
        test_type: Тип теста, для которого предназначен штатив.
        target: Целевое количество пробирок (при достижении может
            потребоваться замена штатива).
    """

    def __init__(self, rack_id: int, test_type: TestType, target: int = 50):
        """Инициализирует целевой штатив.

        Args:
            rack_id: Уникальный ID штатива.
            test_type: Тип теста для этого штатива.
            target: Целевое количество пробирок (по умолчанию 50).
        """
        super().__init__(rack_id)
        self.test_type = test_type
        self.target = target
        self._next_number = 0

    # ---------------------- ОПЕРАЦИИ С ПРОБИРКАМИ ----------------------

    def add_tube(self, tube: TubeInfo) -> TubeInfo:
        """Добавляет пробирку в штатив на следующую свободную позицию.

        Автоматически назначает позицию (destination_number) и
        привязывает пробирку к данному штативу (destination_rack).

        Args:
            tube: Информация о пробирке для размещения.

        Returns:
            Тот же объект TubeInfo с обновлёнными полями назначения.

        Raises:
            ValueError: Если штатив полностью заполнен.
        """
        with self._lock:
            if len(self.tubes) >= self.MAX_TUBES:
                raise ValueError(f"Штатив #{self.rack_id} ({self.test_type.value}) полностью заполнен!")

            tube.destination_rack = self.rack_id
            tube.destination_number = self._next_number

            self.tubes.append(tube)
            self._next_number += 1

            logger.debug(f"Пробирка {tube.barcode} добавлена в штатив #{self.rack_id} на место #{tube.destination_number}")
            return tube

    # ---------------------- СТАТУСЫ И ПРОВЕРКИ ----------------------

    def get_status(self) -> RackStatus:
        """Определяет текущий статус заполненности штатива.

        Returns:
            Статус заполненности с учётом целевого значения.
        """
        with self._lock:
            count = len(self.tubes)
            if count == 0:
                return RackStatus.EMPTY
            elif count >= self.MAX_TUBES:
                return RackStatus.FULL
            elif count >= self.target:
                return RackStatus.TARGET_REACHED
            else:
                return RackStatus.PARTIAL

    def reached_target(self) -> bool:
        """Проверяет, достигнуто ли целевое количество пробирок.

        Returns:
            True, если текущее количество >= target.
        """
        with self._lock:
            return len(self.tubes) >= self.target

    def can_add_tubes(self) -> bool:
        """Проверяет, можно ли добавлять пробирки в штатив.

        Штатив доступен для добавления, если он не заполнен полностью
        и не занят роботом или оператором.

        Returns:
            True, если штатив не полон и свободен.
        """
        with self._lock:
            return (len(self.tubes) < self.MAX_TUBES and
                    self._occupancy == RackOccupancy.FREE)

    def get_available_slots(self) -> int:
        """Возвращает количество свободных мест в штативе.

        Returns:
            Количество позиций, доступных для размещения пробирок.
        """
        with self._lock:
            return self.MAX_TUBES - len(self.tubes)

    def get_next_position(self) -> int:
        """Возвращает номер следующей позиции для размещения пробирки.

        Returns:
            Линейный номер позиции (0-49).
        """
        with self._lock:
            return self._next_number

    # ---------------------- УПРАВЛЕНИЕ ----------------------

    def set_target(self, target: int):
        """Устанавливает новое целевое количество пробирок.

        Args:
            target: Целевое значение (от 0 до MAX_TUBES включительно).

        Raises:
            ValueError: Если target выходит за допустимый диапазон.
        """
        with self._lock:
            if not 0 <= target <= self.MAX_TUBES:
                raise ValueError(f"Целевое значение должно быть между 0 и {self.MAX_TUBES}")
            self.target = target
            logger.info(f"Штатив #{self.rack_id} ({self.test_type.value}): целевое = {target}")

    def reset(self):
        """Сбрасывает штатив в начальное состояние.

        Очищает список пробирок, обнуляет счётчик позиций
        и устанавливает статус занятости FREE.
        """
        with self._lock:
            self.tubes = []
            self._next_number = 0
            self._occupancy = RackOccupancy.FREE
            logger.info(f"Штатив #{self.rack_id} ({self.test_type.value}) сброшен")



class RackSystemManager:
    """Менеджер для централизованного управления системой штативов.

    Координирует работу исходных паллетов (SourceRack) и целевых
    штативов (DestinationRack). Обеспечивает потокобезопасный доступ,
    поиск пробирок, управление целевыми значениями и сброс системы.

    Использует реентерабельную блокировку (RLock) для поддержки
    вложенных вызовов между методами менеджера.

    Attributes:
        source_pallets: Словарь исходных паллетов {pallet_id: SourceRack}.
        destination_racks: Словарь целевых штативов {rack_id: DestinationRack}.
    """

    def __init__(self):
        """Инициализирует менеджер системы штативов с пустыми коллекциями."""
        self.source_pallets: Dict[int, SourceRack] = {}
        self.destination_racks: Dict[int, DestinationRack] = {}
        self._lock = threading.RLock()

        logger.info("RackSystemManager инициализирован")

    # ==================== ИНИЦИАЛИЗАЦИЯ ====================

    def add_source_pallet(self, pallet: SourceRack):
        """Добавляет исходный паллет в систему.

        Если паллет с таким ID уже существует, он будет перезаписан
        с предупреждением в лог.

        Args:
            pallet: Исходный паллет для добавления.
        """
        with self._lock:
            if pallet.rack_id in self.source_pallets:
                logger.warning(f"Паллет П{pallet.rack_id} уже существует, перезапись")
            self.source_pallets[pallet.rack_id] = pallet
            logger.info(f"Добавлен исходный штатив П{pallet.rack_id}")

    def add_destination_rack(self, rack: DestinationRack):
        """Добавляет целевой штатив в систему.

        Если штатив с таким ID уже существует, он будет перезаписан
        с предупреждением в лог.

        Args:
            rack: Целевой штатив для добавления.
        """
        with self._lock:
            if rack.rack_id in self.destination_racks:
                logger.warning(f"Штатив #{rack.rack_id} уже существует, перезапись")
            self.destination_racks[rack.rack_id] = rack
            logger.info(f"Добавлен целевой штатив #{rack.rack_id} ({rack.test_type.value})")

    def initialize_source_pallets(self, pallets_list: List[SourceRack]):
        """Инициализирует набор исходных паллетов.

        Args:
            pallets_list: Список исходных паллетов для регистрации.
        """
        with self._lock:
            for pallet in pallets_list:
                self.add_source_pallet(pallet)
            logger.info(f"Инициализировано {len(pallets_list)} исходных паллетов")

    def initialize_destination_racks(self, racks_list: List[DestinationRack]):
        """Инициализирует набор целевых штативов.

        Args:
            racks_list: Список целевых штативов для регистрации.
        """
        with self._lock:
            for rack in racks_list:
                self.add_destination_rack(rack)
            logger.info(f"Инициализировано {len(racks_list)} целевых штативов")

    def initialize_system(self, pallets_list: List[SourceRack],
                         racks_list: List[DestinationRack]):
        """Инициализирует всю систему: исходные паллеты и целевые штативы.

        Args:
            pallets_list: Список исходных паллетов.
            racks_list: Список целевых штативов.
        """
        with self._lock:
            self.initialize_source_pallets(pallets_list)
            self.initialize_destination_racks(racks_list)
            logger.info("Система полностью инициализирована")

    # ==================== ДОСТУП К ПАЛЛЕТАМ ====================

    def get_source_pallet(self, pallet_id: int) -> Optional[SourceRack]:
        """Возвращает исходный паллет по идентификатору.

        Args:
            pallet_id: ID паллета.

        Returns:
            Объект SourceRack или None, если паллет не найден.
        """
        with self._lock:
            return self.source_pallets.get(pallet_id)

    def get_all_source_pallets(self) -> List[SourceRack]:
        """Возвращает список всех исходных паллетов.

        Returns:
            Список объектов SourceRack.
        """
        with self._lock:
            return list(self.source_pallets.values())

    # ==================== ДОСТУП К ШТАТИВАМ ====================

    def get_destination_rack(self, rack_id: int) -> Optional[DestinationRack]:
        """Возвращает целевой штатив по идентификатору.

        Args:
            rack_id: ID штатива.

        Returns:
            Объект DestinationRack или None, если штатив не найден.
        """
        with self._lock:
            return self.destination_racks.get(rack_id)

    def get_racks_by_type(self, test_type: TestType) -> List[DestinationRack]:
        """Возвращает целевые штативы с указанным типом теста.

        Args:
            test_type: Тип теста для фильтрации.

        Returns:
            Список штативов с совпадающим типом теста.
        """
        with self._lock:
            return [r for r in self.destination_racks.values() if r.test_type == test_type]

    def get_all_destination_racks(self) -> List[DestinationRack]:
        """Возвращает список всех целевых штативов.

        Returns:
            Список объектов DestinationRack.
        """
        with self._lock:
            return list(self.destination_racks.values())

    # ==================== ОПЕРАЦИИ С ПРОБИРКАМИ В ПАЛЛЕТАХ ====================

    def add_scanned_tube(self, pallet_id: int, tube: TubeInfo) -> bool:
        """Добавляет отсканированную пробирку в указанный паллет.

        Args:
            pallet_id: ID паллета для добавления.
            tube: Информация о пробирке.

        Returns:
            True при успешном добавлении, False при ошибке.
        """
        with self._lock:
            pallet = self.get_source_pallet(pallet_id)
            if not pallet:
                logger.error(f"Паллет П{pallet_id} не найден")
                return False

            try:
                pallet.add_scanned_tube(tube)
                return True
            except ValueError as e:
                logger.error(f"Ошибка добавления: {e}")
                return False

    def add_scanned_tubes_batch(self, pallet_id: int, tubes: List[TubeInfo]) -> int:
        """Добавляет пакет отсканированных пробирок в паллет.

        Args:
            pallet_id: ID паллета для добавления.
            tubes: Список пробирок для добавления.

        Returns:
            Количество успешно добавленных пробирок.
        """
        count = 0
        for tube in tubes:
            if self.add_scanned_tube(pallet_id, tube):
                count += 1
        return count

    def mark_tube_sorted(self, pallet_id: int, barcode: str) -> bool:
        """Отмечает пробирку в паллете как отсортированную.

        Args:
            pallet_id: ID паллета, содержащего пробирку.
            barcode: Штрихкод пробирки.

        Returns:
            True при успешной отметке, False если паллет не найден
            или пробирка не найдена/уже отсортирована.
        """
        with self._lock:
            pallet = self.get_source_pallet(pallet_id)
            return pallet.mark_tube_sorted(barcode) if pallet else False

    # ==================== ОПЕРАЦИИ С ПРОБИРКАМИ В ШТАТИВАХ ====================

    def add_tube_to_rack(self, rack_id: int, tube: TubeInfo) -> bool:
        """Добавляет пробирку в целевой штатив.

        Args:
            rack_id: ID целевого штатива.
            tube: Информация о пробирке для размещения.

        Returns:
            True при успешном добавлении, False при ошибке.
        """
        with self._lock:
            rack = self.get_destination_rack(rack_id)
            if not rack:
                logger.error(f"Штатив #{rack_id} не найден")
                return False

            try:
                rack.add_tube(tube)
                logger.debug(f"Пробирка {tube.barcode} добавлена в штатив #{rack_id}")
                return True
            except ValueError as e:
                logger.error(f"Ошибка добавления: {e}")
                return False

    # ==================== ПОИСК ПРОБИРОК ====================

    def find_tube_in_pallets(self, barcode: str) -> Optional[Tuple[int, TubeInfo]]:
        """Ищет пробирку по штрихкоду во всех исходных паллетах.

        Args:
            barcode: Штрихкод пробирки для поиска.

        Returns:
            Кортеж (pallet_id, TubeInfo) или None, если не найдена.
        """
        with self._lock:
            for pallet in self.source_pallets.values():
                tube = pallet.get_tube_by_barcode(barcode)
                if tube:
                    return (pallet.rack_id, tube)
            return None

    def find_tube_in_racks(self, barcode: str) -> Optional[Tuple[int, TubeInfo]]:
        """Ищет пробирку по штрихкоду во всех целевых штативах.

        Args:
            barcode: Штрихкод пробирки для поиска.

        Returns:
            Кортеж (rack_id, TubeInfo) или None, если не найдена.
        """
        with self._lock:
            for rack in self.destination_racks.values():
                tube = rack.get_tube_by_barcode(barcode)
                if tube:
                    return (rack.rack_id, tube)
            return None

    def find_tube_anywhere(self, barcode: str) -> Optional[Tuple[str, int, TubeInfo]]:
        """Ищет пробирку по штрихкоду во всей системе (паллеты + штативы).

        Сначала ищет в исходных паллетах, затем в целевых штативах.

        Args:
            barcode: Штрихкод пробирки для поиска.

        Returns:
            Кортеж (location_type, id, TubeInfo), где location_type
            равен ``'pallet'`` или ``'rack'``. None, если не найдена.
        """
        result = self.find_tube_in_pallets(barcode)
        if result:
            pallet_id, tube = result
            return ('pallet', pallet_id, tube)

        result = self.find_tube_in_racks(barcode)
        if result:
            rack_id, tube = result
            return ('rack', rack_id, tube)

        return None

    # ==================== ПОИСК ДОСТУПНЫХ ШТАТИВОВ ====================

    def find_available_rack(self, test_type: TestType) -> Optional[DestinationRack]:
        """Находит доступный целевой штатив для указанного типа теста.

        Выбирает штатив с наименьшим ID, который ещё не достиг
        целевого значения. Если все штативы данного типа достигли
        target, возвращает None (требуется замена штативов).

        Args:
            test_type: Тип теста для поиска штатива.

        Returns:
            Доступный штатив или None, если все штативы заполнены
            или достигли целевого значения.
        """
        with self._lock:
            candidates = [r for r in self.destination_racks.values()
                         if r.test_type == test_type and not r.is_full()]

            if not candidates:
                return None

            for rack in sorted(candidates, key=lambda r: r.rack_id):
                if not rack.reached_target():
                    return rack

            return None

    def has_available_rack(self, test_type: TestType) -> bool:
        """Проверяет наличие доступного штатива для типа теста.

        Args:
            test_type: Тип теста для проверки.

        Returns:
            True, если есть хотя бы один доступный штатив.
        """
        return self.find_available_rack(test_type) is not None

    def get_available_racks(self, test_type: TestType) -> List[DestinationRack]:
        """Возвращает все доступные штативы для указанного типа теста.

        Args:
            test_type: Тип теста для фильтрации.

        Returns:
            Список штативов, в которые можно добавлять пробирки.
        """
        with self._lock:
            return [r for r in self.destination_racks.values()
                    if r.test_type == test_type and r.can_add_tubes()]

    # ==================== ПРОВЕРКИ СТАТУСОВ ====================

    def are_all_pallets_scanned(self) -> bool:
        """Проверяет, все ли паллеты полностью отсканированы.

        Returns:
            True, если каждый паллет содержит MAX_TUBES пробирок.
        """
        with self._lock:
            return all(p.is_fully_scanned() for p in self.source_pallets.values())

    def are_all_pallets_sorted(self) -> bool:
        """Проверяет, все ли пробирки из паллетов отсортированы.

        Returns:
            True, если ни в одном паллете не осталось неотсортированных
            пробирок.
        """
        with self._lock:
            return all(not p.has_tubes_to_sort() for p in self.source_pallets.values())

    def has_tubes_to_sort(self) -> bool:
        """Проверяет наличие неотсортированных пробирок в любом паллете.

        Returns:
            True, если хотя бы в одном паллете есть пробирки для
            сортировки.
        """
        with self._lock:
            return any(p.has_tubes_to_sort() for p in self.source_pallets.values())

    def get_next_pallet_to_scan(self) -> Optional[SourceRack]:
        """Находит следующий паллет для сканирования.

        Выбирает паллет с наименьшим ID, который ещё не полностью
        отсканирован и свободен для операций.

        Returns:
            Паллет для сканирования или None, если все отсканированы
            или заняты.
        """
        with self._lock:
            for pallet in sorted(self.source_pallets.values(), key=lambda p: p.rack_id):
                if not pallet.is_fully_scanned() and pallet.is_available():
                    return pallet
            return None

    def get_pallets_with_tubes_to_sort(self) -> List[SourceRack]:
        """Возвращает паллеты, содержащие неотсортированные пробирки.

        Returns:
            Список паллетов с пробирками, ожидающими сортировки.
        """
        with self._lock:
            return [p for p in self.source_pallets.values() if p.has_tubes_to_sort()]

    def check_pair_reached_target(self, test_type: TestType) -> bool:
        """Проверяет, достигли ли все штативы типа целевого значения.

        Для типа OTHER проверяется только один штатив. Для остальных
        типов ожидается пара штативов.

        Args:
            test_type: Тип теста для проверки.

        Returns:
            True, если все штативы данного типа достигли target.
        """
        with self._lock:
            type_racks = self.get_racks_by_type(test_type)

            if test_type == TestType.OTHER:
                return type_racks[0].reached_target() if type_racks else False

            if len(type_racks) != 2:
                logger.warning(f"Ожидалось 2 штатива типа {test_type.value}, найдено {len(type_racks)}")
                return False

            return all(r.reached_target() for r in type_racks)

    def get_racks_needing_replacement(self) -> List[DestinationRack]:
        """Возвращает штативы, требующие физической замены.

        Штатив требует замены, если он полностью заполнен или
        помечен статусом WAITING_REPLACE.

        Returns:
            Список штативов, ожидающих замены.
        """
        with self._lock:
            return [r for r in self.destination_racks.values()
                    if r.is_full() or r.get_occupancy() == RackOccupancy.WAITING_REPLACE]

    # ==================== УПРАВЛЕНИЕ ЦЕЛЕВЫМИ ЗНАЧЕНИЯМИ ====================

    def set_rack_target(self, rack_id: int, target: int):
        """Устанавливает целевое количество пробирок для штатива.

        Args:
            rack_id: ID целевого штатива.
            target: Новое целевое значение (0 - MAX_TUBES).
        """
        with self._lock:
            rack = self.get_destination_rack(rack_id)
            if rack:
                rack.set_target(target)
            else:
                logger.error(f"Штатив #{rack_id} не найден")

    def set_targets_by_type(self, test_type: TestType, targets: Dict[int, int]):
        """Устанавливает целевые значения для штативов указанного типа.

        Args:
            test_type: Тип теста для фильтрации штативов.
            targets: Словарь {rack_id: target} с целевыми значениями.
        """
        with self._lock:
            type_racks = self.get_racks_by_type(test_type)
            for rack in type_racks:
                if rack.rack_id in targets:
                    rack.set_target(targets[rack.rack_id])

    # ==================== СБРОС И ЗАМЕНА ====================

    def reset_source_pallet(self, pallet_id: int):
        """Сбрасывает исходный паллет в начальное состояние.

        Args:
            pallet_id: ID паллета для сброса.
        """
        with self._lock:
            pallet = self.get_source_pallet(pallet_id)
            if pallet:
                pallet.reset()
            else:
                logger.error(f"Паллет П{pallet_id} не найден")

    def reset_destination_rack(self, rack_id: int):
        """Сбрасывает целевой штатив в начальное состояние.

        Args:
            rack_id: ID штатива для сброса.
        """
        with self._lock:
            rack = self.get_destination_rack(rack_id)
            if rack:
                rack.reset()
            else:
                logger.error(f"Штатив #{rack_id} не найден")

    def reset_rack_pair(self, test_type: TestType):
        """Сбрасывает все штативы указанного типа теста.

        Args:
            test_type: Тип теста, штативы которого нужно сбросить.
        """
        with self._lock:
            type_racks = self.get_racks_by_type(test_type)
            for rack in type_racks:
                rack.reset()
            logger.info(f"Сброшена пара штативов {test_type.value}")

    def reset_all_source_pallets(self):
        """Сбрасывает все исходные паллеты в начальное состояние."""
        with self._lock:
            for pallet in self.source_pallets.values():
                pallet.reset()
            logger.info("Все исходные паллеты сброшены")

    def reset_all_destination_racks(self):
        """Сбрасывает все целевые штативы в начальное состояние."""
        with self._lock:
            for rack in self.destination_racks.values():
                rack.reset()
            logger.info("Все целевые штативы сброшены")

    def reset_entire_system(self):
        """Сбрасывает всю систему: все паллеты и все целевые штативы."""
        with self._lock:
            self.reset_all_source_pallets()
            self.reset_all_destination_racks()
            logger.info("Вся система сброшена")

    def clear_sorted_tubes(self):
        """Удаляет из памяти отсортированные пробирки во всех паллетах.

        Оставляет только неотсортированные пробирки в каждом паллете.
        """
        with self._lock:
            for pallet in self.source_pallets.values():
                pallet.clear_sorted()
            logger.info("Отсортированные пробирки очищены из паллетов")
