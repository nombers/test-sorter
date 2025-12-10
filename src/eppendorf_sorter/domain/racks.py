# domain/racks.py
"""
Модуль управления штативами для системы сортировки пробирок.
Интегрируется с main.py и обеспечивает thread-safe управление штативами.
"""

from enum import Enum
from typing import Dict, Optional, Tuple, List
import threading
import logging

logger = logging.getLogger(__name__)


class TestType(Enum):
    """Типы тестов"""
    UGI = "pcr-1"
    VPCH = "pcr-2"
    UGI_VPCH = "pcr-1+pcr-2"
    OTHER = "pcr"
    ERROR = "error"
    UNKNOWN = "unknown"


class RackStatus(Enum):
    """Статусы заполненности штативов"""
    EMPTY = "empty"           # Пустой
    PARTIAL = "partial"       # Частично заполнен
    TARGET_REACHED = "target_reached"  # Достигнуто целевое значение
    FULL = "full"             # Полностью заполнен (50/50)


class RackOccupancy(Enum):
    """Статусы занятости штативов"""
    FREE = "free"             # Свободен
    BUSY_ROBOT = "busy_robot" # Занят роботом
    WAITING_REPLACE = "waiting_replace"  # Ожидает замены


class TubeInfo:
    """Информация о пробирке"""
    
    def __init__(self, barcode: str, source_pallet: int, row: int, col: int, 
                 test_type: TestType):
        self.barcode = barcode
        self.source_pallet = source_pallet
        self.row = row
        self.col = col
        self.test_type = test_type
        self.destination_rack: Optional[int] = None
        self.destination_row: Optional[int] = None
        self.destination_col: Optional[int] = None
    
    def __repr__(self):
        return (f"TubeInfo(barcode={self.barcode}, "
                f"source=P{self.source_pallet}[{self.row},{self.col}], "
                f"type={self.test_type.value})")


class DestinationRack:
    """Целевой штатив для размещения пробирок определённого типа"""
    
    MAX_TUBES = 50  # Максимальная вместимость
    
    def __init__(self, rack_id: int, position: Tuple[float, float, float], 
                 test_type: TestType, pallet: int, target: int = 50):
        self.rack_id = rack_id
        self.position = position  # (x, y, z) координаты
        self.test_type = test_type
        self.pallet = pallet  # ID паллета для робота
        self.target = target  # Целевое количество пробирок
        self.current = 0  # Текущее количество
        self.tubes: List[TubeInfo] = []
        self.current_row = 0
        self.current_col = 0
        
        # Статусы
        self._occupancy = RackOccupancy.FREE
        self._lock = threading.Lock()
    
    # ---------------------- ЗАПОЛНЕННОСТЬ ----------------------
    
    def get_status(self) -> RackStatus:
        """Получить статус заполненности"""
        with self._lock:
            if self.current == 0:
                return RackStatus.EMPTY
            elif self.current >= self.MAX_TUBES:
                return RackStatus.FULL
            elif self.current >= self.target:
                return RackStatus.TARGET_REACHED
            else:
                return RackStatus.PARTIAL
    
    def is_empty(self) -> bool:
        """Проверка пустоты"""
        with self._lock:
            return self.current == 0
    
    def is_full(self) -> bool:
        """Физически заполнен (50 пробирок)"""
        with self._lock:
            return self.current >= self.MAX_TUBES
    
    def reached_target(self) -> bool:
        """Достигнуто целевое значение"""
        with self._lock:
            return self.current >= self.target
    
    def can_add_tubes(self) -> bool:
        """Можно ли добавлять пробирки"""
        with self._lock:
            return (self.current < self.MAX_TUBES and 
                    self._occupancy == RackOccupancy.FREE)
    
    def get_available_slots(self) -> int:
        """Количество свободных мест"""
        with self._lock:
            return self.MAX_TUBES - self.current
    
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
        """Занять штатив роботом"""
        self.set_occupancy(RackOccupancy.BUSY_ROBOT)
    
    def release(self):
        """Освободить штатив"""
        self.set_occupancy(RackOccupancy.FREE)
    
    def mark_waiting_replace(self):
        """Отметить ожидание замены"""
        self.set_occupancy(RackOccupancy.WAITING_REPLACE)
    
    def is_available(self) -> bool:
        """Доступен ли штатив для операций"""
        with self._lock:
            return self._occupancy == RackOccupancy.FREE
    
    def is_busy(self) -> bool:
        """Занят ли штатив"""
        with self._lock:
            return self._occupancy != RackOccupancy.FREE
    
    # ---------------------- ОПЕРАЦИИ С ПРОБИРКАМИ ----------------------
    
    def add_tube(self, tube: TubeInfo) -> TubeInfo:
        """Добавить пробирку в штатив"""
        with self._lock:
            if self.current >= self.MAX_TUBES:
                raise ValueError(f"Штатив #{self.rack_id} ({self.test_type.value}) полностью заполнен!")
            
            # Устанавливаем координаты назначения
            tube.destination_rack = self.rack_id
            tube.destination_row = self.current_row
            tube.destination_col = self.current_col
            
            # Добавляем пробирку
            self.tubes.append(tube)
            self.current += 1
            
            # Обновляем текущую позицию
            self.current_col += 1
            if self.current_col >= 5:  # 5 колонок
                self.current_col = 0
                self.current_row += 1
            
            return tube
    
    def get_tube_count(self) -> int:
        """Получить количество пробирок"""
        with self._lock:
            return self.current
    
    def get_barcodes(self) -> List[str]:
        """Получить список баркодов"""
        with self._lock:
            return [tube.barcode for tube in self.tubes]
    
    def get_tubes(self) -> List[TubeInfo]:
        """Получить список пробирок (копия)"""
        with self._lock:
            return self.tubes.copy()
    
    # ---------------------- УПРАВЛЕНИЕ ШТАТИВОМ ----------------------
    
    def reset(self):
        """Сброс штатива после замены"""
        with self._lock:
            self.current = 0
            self.tubes = []
            self.current_row = 0
            self.current_col = 0
            self._occupancy = RackOccupancy.FREE
            logger.info(f"Штатив #{self.rack_id} ({self.test_type.value}) сброшен")
    
    def set_target(self, target: int):
        """Установить новое целевое значение"""
        with self._lock:
            if not 0 <= target <= self.MAX_TUBES:
                raise ValueError(f"Целевое значение должно быть между 0 и {self.MAX_TUBES}")
            self.target = target
            logger.info(f"Штатив #{self.rack_id} ({self.test_type.value}): целевое = {target}")
    
    # ---------------------- ИНФОРМАЦИЯ ----------------------
    
    def get_info(self) -> Dict:
        """Получить полную информацию о штативе"""
        with self._lock:
            return {
                'rack_id': self.rack_id,
                'test_type': self.test_type.value,
                'pallet': self.pallet,
                'position': self.position,
                'current': self.current,
                'target': self.target,
                'max': self.MAX_TUBES,
                'status': self.get_status().value,
                'occupancy': self._occupancy.value,
                'barcodes_count': len(self.tubes)
            }
    
    def __str__(self):
        status = self.get_status()
        occupancy = self._occupancy
        return (f"Штатив #{self.rack_id} ({self.test_type.value}) "
                f"[{self.current}/{self.target}|{self.MAX_TUBES}] "
                f"Статус: {status.value}, Занятость: {occupancy.value}")
    
    def __repr__(self):
        return self.__str__()


class RackManager:
    """
    Менеджер для управления всеми целевыми штативами в системе.
    Обеспечивает thread-safe доступ и координацию.
    """
    
    def __init__(self):
        self.racks: Dict[int, DestinationRack] = {}
        self._lock = threading.RLock()
        logger.info("RackManager инициализирован")
    
    # ---------------------- ИНИЦИАЛИЗАЦИЯ ----------------------
    
    def add_rack(self, rack: DestinationRack):
        """Добавить штатив в систему"""
        with self._lock:
            if rack.rack_id in self.racks:
                logger.warning(f"Штатив #{rack.rack_id} уже существует, перезапись")
            self.racks[rack.rack_id] = rack
            logger.info(f"Добавлен штатив #{rack.rack_id} ({rack.test_type.value})")
    
    def initialize_racks(self, racks_list: List[DestinationRack]):
        """Инициализировать список штативов"""
        with self._lock:
            for rack in racks_list:
                self.add_rack(rack)
            logger.info(f"Инициализировано {len(racks_list)} штативов")
    
    # ---------------------- ДОСТУП К ШТАТИВАМ ----------------------
    
    def get_rack(self, rack_id: int) -> Optional[DestinationRack]:
        """Получить штатив по ID"""
        with self._lock:
            return self.racks.get(rack_id)
    
    def get_racks_by_type(self, test_type: TestType) -> List[DestinationRack]:
        """Получить все штативы определённого типа"""
        with self._lock:
            return [rack for rack in self.racks.values() 
                    if rack.test_type == test_type]
    
    def get_all_racks(self) -> List[DestinationRack]:
        """Получить все штативы"""
        with self._lock:
            return list(self.racks.values())
    
    # ---------------------- ПОИСК ДОСТУПНЫХ ШТАТИВОВ ----------------------
    
    def find_available_rack(self, test_type: TestType) -> Optional[DestinationRack]:
        """
        Найти доступный штатив для типа теста.
        Приоритет: не достигшие целевого, затем не заполненные физически.
        """
        with self._lock:
            candidates = [r for r in self.racks.values() 
                         if r.test_type == test_type and not r.is_full()]
            
            if not candidates:
                return None
            
            # Сначала возвращаем первый не достигший целевого
            for rack in sorted(candidates, key=lambda r: r.rack_id):
                if not rack.reached_target():
                    return rack
            
            # Все достигли целевого, возвращаем первый не заполненный физически
            return candidates[0] if candidates else None
    
    def has_available_rack(self, test_type: TestType) -> bool:
        """Есть ли доступный штатив для типа теста"""
        return self.find_available_rack(test_type) is not None
    
    def get_available_racks(self, test_type: TestType) -> List[DestinationRack]:
        """Получить список всех доступных штативов для типа"""
        with self._lock:
            return [r for r in self.racks.values()
                    if r.test_type == test_type and r.can_add_tubes()]
    
    # ---------------------- ПРОВЕРКИ ЗАПОЛНЕННОСТИ ----------------------
    
    def check_pair_reached_target(self, test_type: TestType) -> bool:
        """
        Проверить, достигли ли оба штатива типа целевого значения.
        Для типа OTHER проверяется только один штатив.
        """
        with self._lock:
            type_racks = self.get_racks_by_type(test_type)
            
            if test_type == TestType.OTHER:
                # Для разного только один штатив
                return type_racks[0].reached_target() if type_racks else False
            
            # Для остальных - оба должны достичь целевого
            if len(type_racks) != 2:
                logger.warning(f"Ожидалось 2 штатива типа {test_type.value}, найдено {len(type_racks)}")
                return False
            
            return all(r.reached_target() for r in type_racks)
    
    def get_racks_needing_replacement(self) -> List[DestinationRack]:
        """Получить список штативов, требующих замены"""
        with self._lock:
            return [r for r in self.racks.values()
                    if r.is_full() or r.get_occupancy() == RackOccupancy.WAITING_REPLACE]
    
    # ---------------------- ОПЕРАЦИИ С ПРОБИРКАМИ ----------------------
    
    def add_tube_to_rack(self, rack_id: int, tube: TubeInfo) -> bool:
        """Добавить пробирку в штатив"""
        with self._lock:
            rack = self.get_rack(rack_id)
            if rack is None:
                logger.error(f"Штатив #{rack_id} не найден")
                return False
            
            try:
                rack.add_tube(tube)
                logger.debug(f"Пробирка {tube.barcode} добавлена в штатив #{rack_id}")
                return True
            except ValueError as e:
                logger.error(f"Ошибка добавления пробирки: {e}")
                return False
    
    # ---------------------- УПРАВЛЕНИЕ ЦЕЛЕВЫМИ ЗНАЧЕНИЯМИ ----------------------
    
    def set_rack_target(self, rack_id: int, target: int):
        """Установить целевое значение для штатива"""
        with self._lock:
            rack = self.get_rack(rack_id)
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
    
    # ---------------------- СБРОС И ЗАМЕНА ----------------------
    
    def reset_rack(self, rack_id: int):
        """Сбросить штатив (после замены)"""
        with self._lock:
            rack = self.get_rack(rack_id)
            if rack:
                rack.reset()
            else:
                logger.error(f"Штатив #{rack_id} не найден")
    
    def reset_pair(self, test_type: TestType):
        """Сбросить пару штативов типа"""
        with self._lock:
            type_racks = self.get_racks_by_type(test_type)
            for rack in type_racks:
                rack.reset()
            logger.info(f"Сброшена пара штативов {test_type.value}")
    
    # ---------------------- СТАТИСТИКА ----------------------
    
    def get_total_tubes(self) -> int:
        """Общее количество пробирок во всех штативах"""
        with self._lock:
            return sum(r.get_tube_count() for r in self.racks.values())
    
    def get_tubes_by_type(self, test_type: TestType) -> int:
        """Количество пробирок определённого типа"""
        with self._lock:
            return sum(r.get_tube_count() 
                      for r in self.racks.values() 
                      if r.test_type == test_type)
    
    def get_all_barcodes(self) -> Dict[TestType, List[str]]:
        """Получить все баркоды, сгруппированные по типам"""
        with self._lock:
            result = {}
            for rack in self.racks.values():
                if rack.test_type not in result:
                    result[rack.test_type] = []
                result[rack.test_type].extend(rack.get_barcodes())
            return result
    
    # ---------------------- ИНФОРМАЦИЯ И ЛОГИРОВАНИЕ ----------------------
    
    def get_system_status(self) -> str:
        """Получить текстовый статус системы"""
        with self._lock:
            lines = []
            lines.append("\n" + "="*80)
            lines.append("СТАТУС СИСТЕМЫ ШТАТИВОВ")
            lines.append("="*80)
            
            # Группируем по типам
            types_order = [TestType.UGI, TestType.VPCH, TestType.UGI_VPCH, TestType.OTHER]
            
            for test_type in types_order:
                type_racks = self.get_racks_by_type(test_type)
                if not type_racks:
                    continue
                
                lines.append(f"\n{test_type.value.upper()}:")
                for rack in sorted(type_racks, key=lambda r: r.rack_id):
                    lines.append(f"  {rack}")
            
            lines.append(f"\nОБЩАЯ СТАТИСТИКА:")
            lines.append(f"  Всего штативов: {len(self.racks)}")
            lines.append(f"  Всего пробирок: {self.get_total_tubes()}")
            
            for test_type in types_order:
                count = self.get_tubes_by_type(test_type)
                if count > 0:
                    lines.append(f"  {test_type.value}: {count} пробирок")
            
            lines.append("="*80)
            return '\n'.join(lines)
    
    def log_status(self):
        """Вывести статус в лог"""
        logger.info(self.get_system_status())
    
    def get_racks_info_for_web(self) -> Dict:
        """Получить информацию о штативах для веб-интерфейса"""
        with self._lock:
            result = {
                'racks': {},
                'statistics': {
                    'total_tubes': self.get_total_tubes(),
                    'by_type': {}
                }
            }
            
            for rack in self.racks.values():
                result['racks'][rack.rack_id] = rack.get_info()
            
            for test_type in [TestType.UGI, TestType.VPCH, TestType.UGI_VPCH, TestType.OTHER]:
                result['statistics']['by_type'][test_type.value] = self.get_tubes_by_type(test_type)
            
            return result


# ---------------------- ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ----------------------

_rack_manager_instance = None
_manager_lock = threading.Lock()


def get_rack_manager() -> RackManager:
    """Получить глобальный экземпляр RackManager (Singleton)"""
    global _rack_manager_instance
    
    if _rack_manager_instance is None:
        with _manager_lock:
            if _rack_manager_instance is None:
                _rack_manager_instance = RackManager()
    
    return _rack_manager_instance


def reset_rack_manager():
    """Сбросить глобальный экземпляр (для тестирования)"""
    global _rack_manager_instance
    with _manager_lock:
        _rack_manager_instance = None
