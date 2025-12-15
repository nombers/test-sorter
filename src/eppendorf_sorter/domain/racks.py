# domain/racks.py
"""
Модуль управления штативами и паллетами для системы сортировки пробирок.
Интегрируется с main.py и обеспечивает thread-safe управление.
"""

from enum import Enum
from typing import Dict, Optional, Tuple, List
import threading
import logging

logger = logging.getLogger(__name__)


# ===================== ENUMS =====================

class TestType(Enum):
    """Типы тестов"""
    UGI = "pcr-1"
    VPCH = "pcr-2"
    UGI_VPCH = "pcr-1+pcr-2"
    OTHER = "pcr"
    ERROR = "error"
    UNKNOWN = "unknown"


class RackStatus(Enum):
    """Статусы заполненности"""
    EMPTY = "empty"           # Пустой
    PARTIAL = "partial"       # Частично заполнен
    TARGET_REACHED = "target_reached"  # Достигнуто целевое значение
    FULL = "full"             # Полностью заполнен (50/50)


class RackOccupancy(Enum):
    """Статусы занятости"""
    FREE = "free"             # Свободен
    BUSY_ROBOT = "busy_robot" # Занят роботом
    WAITING_REPLACE = "waiting_replace"  # Ожидает замены


# ===================== TUBEINFO =====================

class TubeInfo:
    """
    Информация о пробирке.
    """
    
    def __init__(self, barcode: str, source_rack: int, number: int, 
                 test_type: TestType):
        self.barcode = barcode
        self.source_rack = source_rack
        self.number = number  # Номер пробирки в исходном штативе (0-49)
        self.test_type = test_type
        
        # Фактическое размещение (куда поместили пробирку)
        self.destination_rack: Optional[int] = None  # ID целевого штатива
        self.destination_number: Optional[int] = None  # Позиция в целевом штативе (0-49)
    
    # ========== СВОЙСТВА ДЛЯ ИСТОЧНИКА ==========
    
    @property
    def row(self) -> int:
        """Получить ряд из номера (0-9)"""
        return self.number // 5
    
    @property
    def col(self) -> int:
        """Получить колонку из номера (0-4)"""
        return self.number % 5
    
    # ========== СВОЙСТВА ДЛЯ ФАКТИЧЕСКОГО РАЗМЕЩЕНИЯ ==========
    
    @property
    def destination_row(self) -> Optional[int]:
        """Получить ряд фактического назначения"""
        if self.destination_number is None:
            return None
        return self.destination_number // 5
    
    @property
    def destination_col(self) -> Optional[int]:
        """Получить колонку фактического назначения"""
        if self.destination_number is None:
            return None
        return self.destination_number % 5
    
    # ========== ПРОВЕРКИ ==========
    
    @property
    def is_placed(self) -> bool:
        """Проверка, размещена ли пробирка физически"""
        return self.destination_rack is not None
    
    # ========== УТИЛИТЫ ==========
    
    @staticmethod
    def number_from_position(row: int, col: int) -> int:
        """Получить номер из позиции (row, col)"""
        return row * 5 + col
    
    @staticmethod
    def position_from_number(number: int) -> Tuple[int, int]:
        """Получить позицию (row, col) из номера"""
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
    """
    Базовый класс для штативов.
    Содержит общую логику управления пробирками и занятости.
    """
    
    MAX_TUBES = 50  # Максимальная вместимость
    
    def __init__(self, rack_id: int):
        self.rack_id = rack_id
        self.tubes: List[TubeInfo] = []
        self._occupancy = RackOccupancy.FREE
        self._lock = threading.Lock()
    
    # ---------------------- ЗАНЯТОСТЬ ----------------------
    
    def get_occupancy(self) -> RackOccupancy:
        """Получить статус занятости"""
        with self._lock:
            return self._occupancy
    
    def set_occupancy(self, occupancy: RackOccupancy):
        """Установить статус занятости"""
        with self._lock:
            if not isinstance(occupancy, RackOccupancy):
                raise ValueError("Статус занятости должен соответствовать RackOccupancy")
            self._occupancy = occupancy
    
    def occupy(self):
        """Занять роботом"""
        self.set_occupancy(RackOccupancy.BUSY_ROBOT)
    
    def release(self):
        """Освободить"""
        self.set_occupancy(RackOccupancy.FREE)
    
    def mark_waiting_replace(self):
        """Отметить ожидание замены"""
        self.set_occupancy(RackOccupancy.WAITING_REPLACE)
    
    def is_available(self) -> bool:
        """Доступен ли для операций"""
        with self._lock:
            return self._occupancy == RackOccupancy.FREE
    
    def is_busy(self) -> bool:
        """Занят ли"""
        with self._lock:
            return self._occupancy != RackOccupancy.FREE
    
    # ---------------------- РАБОТА С ПРОБИРКАМИ ----------------------
    
    def get_tube_count(self) -> int:
        """Получить количество пробирок"""
        with self._lock:
            return len(self.tubes)
    
    def get_barcodes(self) -> List[str]:
        """Получить список баркодов"""
        with self._lock:
            return [tube.barcode for tube in self.tubes]
    
    def get_tubes(self) -> List[TubeInfo]:
        """Получить список пробирок (копия)"""
        with self._lock:
            return self.tubes.copy()
    
    def get_tube_by_barcode(self, barcode: str) -> Optional[TubeInfo]:
        """Найти пробирку по баркоду"""
        with self._lock:
            for tube in self.tubes:
                if tube.barcode == barcode:
                    return tube
            return None
    
    def has_barcode(self, barcode: str) -> bool:
        """Проверить наличие пробирки"""
        return self.get_tube_by_barcode(barcode) is not None
    
    # ---------------------- СТАТУСЫ ----------------------
    
    def is_empty(self) -> bool:
        """Пустой ли"""
        with self._lock:
            return len(self.tubes) == 0
    
    def is_full(self) -> bool:
        """Полностью заполнен"""
        with self._lock:
            return len(self.tubes) >= self.MAX_TUBES
    
    # ---------------------- СБРОС ----------------------
    
    def reset(self):
        """Сброс (после замены)"""
        with self._lock:
            self.tubes = []
            self._occupancy = RackOccupancy.FREE
            logger.info(f"Rack #{self.rack_id} сброшен")


# ===================== SOURCE Rack =====================

class SourceRack(BaseRack):
    """
    Исходный штатив с пробирками для сканирования и сортировки.
    Расширяет BaseRack функциональностью сканирования и отслеживания сортировки.
    """
    
    def __init__(self, pallet_id: int):
        """
        Args:
            pallet_id: Уникальный ID паллета (0, 1, 2, ...)
        """
        super().__init__(pallet_id)
        self._sorted_count = 0  # Сколько отсортировано
    
    # ---------------------- ОПЕРАЦИИ С ПРОБИРКАМИ ----------------------
    
    def add_scanned_tube(self, tube: TubeInfo):
        """Добавить отсканированную пробирку"""
        with self._lock:
            if tube.source_rack != self.rack_id:
                raise ValueError(f"Пробирка принадлежит паллету {tube.source_rack}, а не {self.rack_id}")
            
            # Проверяем дубликат
            if self.has_barcode(tube.barcode):
                logger.warning(f"Пробирка {tube.barcode} уже в паллете П{self.rack_id}")
                return
            
            self.tubes.append(tube)
            logger.debug(f"Пробирка {tube.barcode} добавлена в П{self.rack_id} ({len(self.tubes)}/50)")
    
    def add_scanned_tubes(self, tubes: List[TubeInfo]):
        """Добавить несколько пробирок"""
        for tube in tubes:
            self.add_scanned_tube(tube)
    
    def mark_tube_sorted(self, barcode: str) -> bool:
        """Отметить пробирку как отсортированную"""
        with self._lock:
            tube = self.get_tube_by_barcode(barcode)
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
        """Получить статус заполненности"""
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
        """Количество отсортированных пробирок"""
        with self._lock:
            return self._sorted_count
    
    def get_remaining_count(self) -> int:
        """Количество пробирок, ожидающих сортировки"""
        with self._lock:
            return len(self.tubes) - self._sorted_count
    
    def is_fully_scanned(self) -> bool:
        """Все ожидаемые пробирки отсканированы"""
        with self._lock:
            return len(self.tubes) >= self.MAX_TUBES
    
    def has_tubes_to_sort(self) -> bool:
        """Есть ли пробирки для сортировки"""
        with self._lock:
            return self._sorted_count < len(self.tubes)
    
    def get_unsorted_tubes(self) -> List[TubeInfo]:
        """Получить неотсортированные пробирки"""
        with self._lock:
            return [t for t in self.tubes if t.destination_rack is None]
    
    def get_tubes_by_type(self, test_type: TestType) -> List[TubeInfo]:
        """Получить пробирки определённого типа"""
        with self._lock:
            return [t for t in self.tubes if t.test_type == test_type]
    
    # ---------------------- ПРОГРЕСС И СТАТИСТИКА ----------------------
    
    def get_scan_progress(self) -> float:
        """Прогресс сканирования (0.0 - 1.0)"""
        with self._lock:
            if self.MAX_TUBES == 0:
                return 1.0
            return min(len(self.tubes) / self.MAX_TUBES, 1.0)
    
    def get_sort_progress(self) -> float:
        """Прогресс сортировки (0.0 - 1.0)"""
        with self._lock:
            if len(self.tubes) == 0:
                return 0.0
            return self._sorted_count / len(self.tubes)
    
    def get_statistics_by_type(self) -> Dict[TestType, int]:
        """Статистика по типам тестов"""
        with self._lock:
            stats = {}
            for tube in self.tubes:
                stats[tube.test_type] = stats.get(tube.test_type, 0) + 1
            return stats
    
    # ---------------------- УПРАВЛЕНИЕ ----------------------
    
    def reset(self):
        """Сброс паллета"""
        with self._lock:
            self.tubes = []
            self._sorted_count = 0
            self._occupancy = RackOccupancy.FREE
            logger.info(f"Паллет П{self.rack_id} сброшен")
    
    def clear_sorted(self):
        """Очистить отсортированные пробирки из памяти"""
        with self._lock:
            self.tubes = [t for t in self.tubes if t.destination_rack is None]
            logger.debug(f"Очищены отсортированные пробирки из П{self.rack_id}")
    


# ===================== DESTINATION RACK =====================

class DestinationRack(BaseRack):
    """
    Целевой штатив для размещения пробирок определённого типа.
    Расширяет BaseRack функциональностью целевых значений и нумерации.
    """
    
    def __init__(self, rack_id: int, test_type: TestType, target: int = 50):
        """
        Args:
            rack_id: Уникальный ID штатива
            test_type: Тип теста для этого штатива
            target: Целевое количество пробирок
        """
        super().__init__(rack_id)
        self.test_type = test_type
        self.target = target
        self._next_number = 0  # Следующий номер для размещения пробирки
    
    # ---------------------- ОПЕРАЦИИ С ПРОБИРКАМИ ----------------------
    
    def add_tube(self, tube: TubeInfo) -> TubeInfo:
        """Добавить пробирку в штатив"""
        with self._lock:
            if len(self.tubes) >= self.MAX_TUBES:
                raise ValueError(f"Штатив #{self.rack_id} ({self.test_type.value}) полностью заполнен!")
            
            # Устанавливаем место назначения
            tube.destination_rack = self.rack_id
            tube.destination_number = self._next_number
            
            # Добавляем пробирку
            self.tubes.append(tube)
            self._next_number += 1
            
            logger.debug(f"Пробирка {tube.barcode} добавлена в штатив #{self.rack_id} на место #{tube.destination_number}")
            return tube
    
    # ---------------------- СТАТУСЫ И ПРОВЕРКИ ----------------------
    
    def get_status(self) -> RackStatus:
        """Получить статус заполненности"""
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
        """Достигнуто целевое значение"""
        with self._lock:
            return len(self.tubes) >= self.target
    
    def can_add_tubes(self) -> bool:
        """Можно ли добавлять пробирки"""
        with self._lock:
            return (len(self.tubes) < self.MAX_TUBES and 
                    self._occupancy == RackOccupancy.FREE)
    
    def get_available_slots(self) -> int:
        """Количество свободных мест"""
        with self._lock:
            return self.MAX_TUBES - len(self.tubes)
    
    # ---------------------- УПРАВЛЕНИЕ ----------------------
    
    def set_target(self, target: int):
        """Установить новое целевое значение"""
        with self._lock:
            if not 0 <= target <= self.MAX_TUBES:
                raise ValueError(f"Целевое значение должно быть между 0 и {self.MAX_TUBES}")
            self.target = target
            logger.info(f"Штатив #{self.rack_id} ({self.test_type.value}): целевое = {target}")
    
    def reset(self):
        """Сброс штатива"""
        with self._lock:
            self.tubes = []
            self._next_number = 0
            self._occupancy = RackOccupancy.FREE
            logger.info(f"Штатив #{self.rack_id} ({self.test_type.value}) сброшен")
    


class RackSystemManager:
    """
    Единый менеджер для управления всей системой штативов.
    Обеспечивает thread-safe доступ и координацию работы.
    """
    
    def __init__(self):
        # Исходные штативы
        self.source_pallets: Dict[int, SourceRack] = {}
        
        # Целевые штативы
        self.destination_racks: Dict[int, DestinationRack] = {}
        
        # Единая блокировка для всей системы
        self._lock = threading.RLock()
        
        logger.info("RackSystemManager инициализирован")
    
    # ==================== ИНИЦИАЛИЗАЦИЯ ====================
    
    def add_source_pallet(self, pallet: SourceRack):
        """Добавить исходный штатив"""
        with self._lock:
            if pallet.rack_id in self.source_pallets:
                logger.warning(f"Паллет П{pallet.rack_id} уже существует, перезапись")
            self.source_pallets[pallet.rack_id] = pallet
            logger.info(f"Добавлен исходный штатив П{pallet.rack_id}")
    
    def add_destination_rack(self, rack: DestinationRack):
        """Добавить целевой штатив"""
        with self._lock:
            if rack.rack_id in self.destination_racks:
                logger.warning(f"Штатив #{rack.rack_id} уже существует, перезапись")
            self.destination_racks[rack.rack_id] = rack
            logger.info(f"Добавлен целевой штатив #{rack.rack_id} ({rack.test_type.value})")
    
    def initialize_source_pallets(self, pallets_list: List[SourceRack]):
        """Инициализировать исходные паллеты"""
        with self._lock:
            for pallet in pallets_list:
                self.add_source_pallet(pallet)
            logger.info(f"Инициализировано {len(pallets_list)} исходных паллетов")
    
    def initialize_destination_racks(self, racks_list: List[DestinationRack]):
        """Инициализировать целевые штативы"""
        with self._lock:
            for rack in racks_list:
                self.add_destination_rack(rack)
            logger.info(f"Инициализировано {len(racks_list)} целевых штативов")
    
    def initialize_system(self, pallets_list: List[SourceRack], 
                         racks_list: List[DestinationRack]):
        """Инициализировать всю систему (паллеты + штативы)"""
        with self._lock:
            self.initialize_source_pallets(pallets_list)
            self.initialize_destination_racks(racks_list)
            logger.info("Система полностью инициализирована")
    
    # ==================== ДОСТУП К ПАЛЛЕТАМ ====================
    
    def get_source_pallet(self, pallet_id: int) -> Optional[SourceRack]:
        """Получить исходный паллет по ID"""
        with self._lock:
            return self.source_pallets.get(pallet_id)
    
    def get_all_source_pallets(self) -> List[SourceRack]:
        """Получить все исходные паллеты"""
        with self._lock:
            return list(self.source_pallets.values())
    
    # ==================== ДОСТУП К ШТАТИВАМ ====================
    
    def get_destination_rack(self, rack_id: int) -> Optional[DestinationRack]:
        """Получить целевой штатив по ID"""
        with self._lock:
            return self.destination_racks.get(rack_id)
    
    def get_racks_by_type(self, test_type: TestType) -> List[DestinationRack]:
        """Получить штативы определённого типа"""
        with self._lock:
            return [r for r in self.destination_racks.values() if r.test_type == test_type]
    
    def get_all_destination_racks(self) -> List[DestinationRack]:
        """Получить все целевые штативы"""
        with self._lock:
            return list(self.destination_racks.values())
    
    # ==================== ОПЕРАЦИИ С ПРОБИРКАМИ В ПАЛЛЕТАХ ====================
    
    def add_scanned_tube(self, pallet_id: int, tube: TubeInfo) -> bool:
        """Добавить отсканированную пробирку в паллет"""
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
        """Добавить пакет пробирок. Возвращает количество успешно добавленных."""
        count = 0
        for tube in tubes:
            if self.add_scanned_tube(pallet_id, tube):
                count += 1
        return count
    
    def mark_tube_sorted(self, pallet_id: int, barcode: str) -> bool:
        """Отметить пробирку в паллете как отсортированную"""
        with self._lock:
            pallet = self.get_source_pallet(pallet_id)
            return pallet.mark_tube_sorted(barcode) if pallet else False
    
    # ==================== ОПЕРАЦИИ С ПРОБИРКАМИ В ШТАТИВАХ ====================
    
    def add_tube_to_rack(self, rack_id: int, tube: TubeInfo) -> bool:
        """Добавить пробирку в целевой штатив"""
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
        """
        Найти пробирку по баркоду во всех паллетах.
        Возвращает (pallet_id, tube) или None.
        """
        with self._lock:
            for pallet in self.source_pallets.values():
                tube = pallet.get_tube_by_barcode(barcode)
                if tube:
                    return (pallet.rack_id, tube)
            return None
    
    def find_tube_in_racks(self, barcode: str) -> Optional[Tuple[int, TubeInfo]]:
        """
        Найти пробирку по баркоду во всех штативах.
        Возвращает (rack_id, tube) или None.
        """
        with self._lock:
            for rack in self.destination_racks.values():
                tube = rack.get_tube_by_barcode(barcode)
                if tube:
                    return (rack.rack_id, tube)
            return None
    
    def find_tube_anywhere(self, barcode: str) -> Optional[Tuple[str, int, TubeInfo]]:
        """
        Найти пробирку везде (паллеты + штативы).
        Возвращает (location_type, id, tube) где location_type = 'pallet' или 'rack'.
        """
        # Сначала ищем в паллетах
        result = self.find_tube_in_pallets(barcode)
        if result:
            pallet_id, tube = result
            return ('pallet', pallet_id, tube)
        
        # Затем в штативах
        result = self.find_tube_in_racks(barcode)
        if result:
            rack_id, tube = result
            return ('rack', rack_id, tube)
        
        return None
    
    # ==================== ПОИСК ДОСТУПНЫХ ШТАТИВОВ ====================
    
    def find_available_rack(self, test_type: TestType) -> Optional[DestinationRack]:
        """
        Найти доступный штатив для типа теста.
        Приоритет: не достигшие целевого, затем не заполненные физически.
        """
        with self._lock:
            candidates = [r for r in self.destination_racks.values() 
                         if r.test_type == test_type and not r.is_full()]
            
            if not candidates:
                return None
            
            # Сначала не достигшие целевого
            for rack in sorted(candidates, key=lambda r: r.rack_id):
                if not rack.reached_target():
                    return rack
            
            # Все достигли целевого, возвращаем первый не заполненный
            return candidates[0] if candidates else None
    
    def has_available_rack(self, test_type: TestType) -> bool:
        """Есть ли доступный штатив для типа теста"""
        return self.find_available_rack(test_type) is not None
    
    def get_available_racks(self, test_type: TestType) -> List[DestinationRack]:
        """Список всех доступных штативов для типа"""
        with self._lock:
            return [r for r in self.destination_racks.values()
                    if r.test_type == test_type and r.can_add_tubes()]
    
    # ==================== ПРОВЕРКИ СТАТУСОВ ====================
    
    # Паллеты
    def are_all_pallets_scanned(self) -> bool:
        """Все паллеты полностью отсканированы"""
        with self._lock:
            return all(p.is_fully_scanned() for p in self.source_pallets.values())
    
    def are_all_pallets_sorted(self) -> bool:
        """Все пробирки из паллетов отсортированы"""
        with self._lock:
            return all(not p.has_tubes_to_sort() for p in self.source_pallets.values())
    
    def has_tubes_to_sort(self) -> bool:
        """Есть ли пробирки в паллетах для сортировки"""
        with self._lock:
            return any(p.has_tubes_to_sort() for p in self.source_pallets.values())
    
    def get_next_pallet_to_scan(self) -> Optional[SourceRack]:
        """Получить следующий паллет для сканирования"""
        with self._lock:
            for pallet in sorted(self.source_pallets.values(), key=lambda p: p.rack_id):
                if not pallet.is_fully_scanned() and pallet.is_available():
                    return pallet
            return None
    
    def get_pallets_with_tubes_to_sort(self) -> List[SourceRack]:
        """Получить паллеты с пробирками для сортировки"""
        with self._lock:
            return [p for p in self.source_pallets.values() if p.has_tubes_to_sort()]
    
    # Штативы
    def check_pair_reached_target(self, test_type: TestType) -> bool:
        """
        Проверить, достигли ли оба штатива типа целевого значения.
        Для типа OTHER проверяется только один штатив.
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
        """Список штативов, требующих замены"""
        with self._lock:
            return [r for r in self.destination_racks.values()
                    if r.is_full() or r.get_occupancy() == RackOccupancy.WAITING_REPLACE]
    
    # ==================== УПРАВЛЕНИЕ ЦЕЛЕВЫМИ ЗНАЧЕНИЯМИ ====================
    
    def set_rack_target(self, rack_id: int, target: int):
        """Установить целевое значение для штатива"""
        with self._lock:
            rack = self.get_destination_rack(rack_id)
            if rack:
                rack.set_target(target)
            else:
                logger.error(f"Штатив #{rack_id} не найден")
    
    def set_targets_by_type(self, test_type: TestType, targets: Dict[int, int]):
        """Установить целевые значения для штативов типа"""
        with self._lock:
            type_racks = self.get_racks_by_type(test_type)
            for rack in type_racks:
                if rack.rack_id in targets:
                    rack.set_target(targets[rack.rack_id])
    
    # ==================== СБРОС И ЗАМЕНА ====================
    
    def reset_source_pallet(self, pallet_id: int):
        """Сбросить исходный паллет"""
        with self._lock:
            pallet = self.get_source_pallet(pallet_id)
            if pallet:
                pallet.reset()
            else:
                logger.error(f"Паллет П{pallet_id} не найден")
    
    def reset_destination_rack(self, rack_id: int):
        """Сбросить целевой штатив"""
        with self._lock:
            rack = self.get_destination_rack(rack_id)
            if rack:
                rack.reset()
            else:
                logger.error(f"Штатив #{rack_id} не найден")
    
    def reset_rack_pair(self, test_type: TestType):
        """Сбросить пару штативов типа"""
        with self._lock:
            type_racks = self.get_racks_by_type(test_type)
            for rack in type_racks:
                rack.reset()
            logger.info(f"Сброшена пара штативов {test_type.value}")
    
    def reset_all_source_pallets(self):
        """Сбросить все исходные паллеты"""
        with self._lock:
            for pallet in self.source_pallets.values():
                pallet.reset()
            logger.info("Все исходные паллеты сброшены")
    
    def reset_all_destination_racks(self):
        """Сбросить все целевые штативы"""
        with self._lock:
            for rack in self.destination_racks.values():
                rack.reset()
            logger.info("Все целевые штативы сброшены")
    
    def reset_entire_system(self):
        """Сбросить всю систему (паллеты + штативы)"""
        with self._lock:
            self.reset_all_source_pallets()
            self.reset_all_destination_racks()
            logger.info("Вся система сброшена")
    
    def clear_sorted_tubes(self):
        """Очистить отсортированные пробирки из паллетов"""
        with self._lock:
            for pallet in self.source_pallets.values():
                pallet.clear_sorted()
            logger.info("Отсортированные пробирки очищены из паллетов")