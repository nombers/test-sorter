# src/eppendorf_sorter/orchestration/robot_logic.py
"""
Главная логика сортировочного робота.
Отвечает за сканирование штативов и сортировку пробирок.

ГРУППОВОЕ СКАНИРОВАНИЕ:
=======================
Сканер возвращает несколько баркодов за одну итерацию (разделённые ';'):
- Группа 1: колонки 0, 1, 2 (3 пробирки) -> "barcode1;barcode2;barcode3"
- Группа 2: колонки 3, 4 (2 пробирки) -> "barcode1;barcode2"

Каждый ряд (10 рядов в штативе) сканируется за 2 итерации робота.

ПРОТОКОЛ ВЗАИМОДЕЙСТВИЯ С РОБОТОМ:
==================================

SCANNING_ITERATION:
1. Python: ждёт R[1] = 0 (робот готов)
2. Python: устанавливает SR[3] = "PP NN" (паллет, первая позиция группы)
3. Python: устанавливает SR[1] = "SCANNING_ITERATION"
4. Python: устанавливает R[1] = 1 (запуск итерации)
5. Робот: едет в позицию, ставит R[2] = 1 (готов к сканированию)
6. Python: видит R[2] = 1, выполняет сканирование
7. Python: ставит R[2] = 0 (сканирование завершено)
8. Робот: видит R[2] = 0, ставит R[1] = 2 (итерация завершена)
9. Робот: сбрасывает R[1] = 0, R[2] = 0
   (Python ждёт R[1] = 0 для следующей итерации)
"""
import time
import threading
import logging
from typing import List, Optional, Tuple

from src.eppendorf_sorter.devices import CellRobot, Scanner
from src.eppendorf_sorter.config.robot_config import load_robot_config
from src.eppendorf_sorter.domain.racks import (
    RackSystemManager,
    TestType,
    TubeInfo,
)
from src.eppendorf_sorter.lis import LISClient
from .robot_protocol import NR, NR_VAL, SR, SR_VAL
from .operator_input import OperatorInputHandler


ROBOT_CFG = load_robot_config()
SCANNER_CFG = ROBOT_CFG.scanner
LIS_CFG = ROBOT_CFG.lis


class RobotThread(threading.Thread):
    """
    Поток, выполняющий основную логику робота-сортировщика.
    """

    def __init__(
        self,
        rack_manager: RackSystemManager,
        robot: CellRobot,
        scanner: Scanner,
        lis_client: LISClient,
        logger: logging.Logger,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="RobotThread", daemon=True)
        self.rack_manager = rack_manager
        self.robot = robot
        self.scanner = scanner
        self.lis_client = lis_client
        self.logger = logger
        self.stop_event = stop_event
        
        self.operator_input = OperatorInputHandler(logger, stop_event)
        self.operator_input.set_status_callback(self._get_system_status)

    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================

    def _wait_until(self, condition, poll: float = 0.05, timeout: float = 30.0) -> bool:
        """
        Ожидает выполнения условия с проверкой stop_event.
        """
        start_time = time.time()
        while not self.stop_event.is_set():
            try:
                if condition():
                    return True
            except Exception as e:
                self.logger.warning(f"Ошибка при проверке условия: {e}")
                
        return False

    def _wait_robot_ready(self, timeout: float = 15.0) -> bool:
        """
        Ожидает готовности робота (R[1] = 0).
        """
        self.logger.debug("Ожидание готовности робота (R[1] = 0)...")
        result = self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.ready,
            timeout=timeout
        )
        if result:
            self.logger.debug("Робот готов")
        return result

    def _wait_scan_ready(self, timeout: float = 20.0) -> bool:
        """
        Ожидает готовности робота к сканированию (R[2] = 1).
        """
        self.logger.debug("Ожидание позиционирования (R[2] = 1)...")
        result = self._wait_until(
            lambda: self.robot.get_number_register(NR.scan_status) == NR_VAL.scan_good,
            timeout=timeout
        )
        if result:
            self.logger.debug("Робот в позиции сканирования")
        return result

    def _wait_iteration_complete(self, timeout: float = 60.0) -> bool:
        """
        Ожидает завершения итерации (R[1] = 2).
        """
        self.logger.debug("Ожидание завершения итерации (R[1] = 2)...")
        result = self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed,
            timeout=timeout
        )
        if result:
            self.logger.debug("Итерация завершена")
        return result

    def _parse_barcodes(self, raw_barcode: str) -> List[str]:
        """
        Парсит строку с баркодами (разделённые ';').
        
        Args:
            raw_barcode: Строка вида "barcode1;barcode2;barcode3" или "NoRead"
            
        Returns:
            Список баркодов (пустые строки для NoRead)
        """
        if not raw_barcode or raw_barcode == "NoRead":
            return []
        
        # Разделяем по ';' и очищаем
        barcodes = [b.strip() for b in raw_barcode.split(';')]
        # Фильтруем пустые и NoRead
        return [b for b in barcodes if b and b != "NoRead"]

    # ==================== ФАЗА СКАНИРОВАНИЯ ====================

    def _scan_position_group(
        self, pallet_id: int, row: int, col_start: int, col_end: int
    ) -> List[TubeInfo]:
        """
        Сканирует группу позиций в одной строке штатива.
        
        Сканер возвращает несколько баркодов в одной строке, разделённых ';'.
        Например: "2701200911;2708770050;2707602822"
        
        ПРОТОКОЛ:
        1. Ждём R[1] = 0 (робот готов)
        2. Устанавливаем SR[3] = "PP NN" (паллет, первая позиция)
        3. Устанавливаем SR[1] = "SCANNING_ITERATION"
        4. Устанавливаем R[1] = 1 (запуск)
        5. Ждём R[2] = 1 (робот в позиции)
        6. Выполняем сканирование (получаем группу баркодов)
        7. Устанавливаем R[2] = 0 (сканирование завершено)
        8. Ждём R[1] = 2 (итерация завершена)
        9. Робот сам сбрасывает R[1] = 0

        Args:
            pallet_id: ID паллета (1 или 2)
            row: Номер ряда (0-9)
            col_start: Начальная колонка (включительно)
            col_end: Конечная колонка (не включительно)

        Returns:
            Список TubeInfo для найденных пробирок
        """
        positions = [row * 5 + col for col in range(col_start, col_end)]
        group_size = len(positions)
        first_position = positions[0]

        self.logger.debug(
            f"Сканирование П{pallet_id} ряд {row} колонки {col_start}-{col_end-1} "
            f"(позиции {positions})"
        )

        # 1. Ждём готовности робота
        if not self._wait_robot_ready(timeout=15.0):
            self.logger.error(f"Робот не готов для сканирования П{pallet_id} ряд {row}")
            return []

        # 2. Формируем данные: "PP NN" - паллет и первая позиция группы
        scan_data = f"{pallet_id:02d} {first_position:02d}"
        self.robot.set_string_register(SR.scan_data, scan_data)
        self.logger.debug(f"SR[3: SCAN_DATA] = '{scan_data}'")

        # 3. Устанавливаем тип итерации
        self.robot.set_string_register(SR.iteration_type, SR_VAL.scanning)
        self.logger.debug(f"SR[1: ITERATION_TYPE] = '{SR_VAL.scanning}'")

        # 4. ЗАПУСКАЕМ ИТЕРАЦИЮ
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.started)
        self.logger.debug("R[1] = 1 (итерация запущена)")

        # 5. Ждём готовности к сканированию (R[2] = 1)
        if not self._wait_scan_ready(timeout=20.0):
            self.logger.warning(f"Таймаут позиционирования П{pallet_id} ряд {row}")
            return []

        # 6. Выполняем сканирование
        raw_barcode, recv_time = self.scanner.scan(timeout=SCANNER_CFG.timeout)
        self.logger.debug(f"Сканер вернул: '{raw_barcode}' за {recv_time:.3f}с")

        # 7. Сигнализируем роботу что сканирование завершено
        self.robot.set_number_register(NR.scan_status, NR_VAL.scan_reset)
        self.logger.debug("R[2] = 0 (сканирование завершено)")

        # # 8. Ждём завершения итерации
        # if not self._wait_iteration_complete(timeout=15.0):
        #     self.logger.warning(f"Таймаут завершения итерации П{pallet_id} ряд {row}")
        #     # Продолжаем, чтобы не потерять данные

        # Робот сам сбрасывает R[1] = 0

        # Парсим баркоды (разделённые ';')
        barcodes = self._parse_barcodes(raw_barcode)
        
        # Создаём TubeInfo для каждого баркода
        tubes: List[TubeInfo] = []
        
        for i, barcode in enumerate(barcodes):
            if i >= group_size:
                self.logger.warning(f"Получено больше баркодов ({len(barcodes)}) чем позиций ({group_size})")
                break
            
            position = positions[i]
            self.logger.info(f"✓ П{pallet_id}[{position}] -> {barcode}")
            
            tube = TubeInfo(
                barcode=barcode,
                source_rack=pallet_id,
                number=position,
                test_type=TestType.UNKNOWN
            )
            tubes.append(tube)
        
        # Логируем пустые позиции
        if len(barcodes) < group_size:
            for i in range(len(barcodes), group_size):
                position = positions[i]
                self.logger.debug(f"П{pallet_id}[{position}] - пусто")

        return tubes

    def _scan_all_source_racks(self) -> List[TubeInfo]:
        """
        ФАЗА 1: Сканирование всех пробирок из исходных штативов.
        
        Каждый ряд сканируется за 2 итерации:
        - Группа 1: колонки 0, 1, 2 (3 пробирки)
        - Группа 2: колонки 3, 4 (2 пробирки)
        
        Returns:
            Список TubeInfo со всей информацией о пробирках
        """
        self.logger.info("\n" + "=" * 60)
        self.logger.info("ФАЗА 1: СКАНИРОВАНИЕ ИСХОДНЫХ ШТАТИВОВ")
        self.logger.info("=" * 60 + "\n")
        
        all_scanned_tubes: List[TubeInfo] = []
        
        # Получаем все исходные штативы
        source_pallets = self.rack_manager.get_all_source_pallets()
        
        for pallet in source_pallets:
            if self.stop_event.is_set():
                break
            
            pallet_id = pallet.pallet_id
            self.logger.info(f"\n--- Сканирование паллета П{pallet_id} ---")
            
            # Занимаем паллет
            pallet.occupy()
            pallet_tubes: List[TubeInfo] = []
            
            try:
                # Сканируем 10 рядов
                for row in range(10):
                    if self.stop_event.is_set():
                        break

                    # Группа 1: колонки 0, 1, 2 (3 пробирки)
                    tubes_group1 = self._scan_position_group(
                        pallet_id, row, col_start=0, col_end=3
                    )
                    pallet_tubes.extend(tubes_group1)

                    # Проверка паузы
                    if not self.operator_input.check_pause():
                        break

                    # Группа 2: колонки 3, 4 (2 пробирки)
                    tubes_group2 = self._scan_position_group(
                        pallet_id, row, col_start=3, col_end=5
                    )
                    pallet_tubes.extend(tubes_group2)

                    # Проверка паузы
                    if not self.operator_input.check_pause():
                        break
                    
                    # Прогресс каждые 2 ряда
                    if (row + 1) % 2 == 0:
                        self.logger.info(
                            f"П{pallet_id}: ряд {row + 1}/10, "
                            f"найдено {len(pallet_tubes)} пробирок"
                        )
            
            finally:
                pallet.release()
            
            all_scanned_tubes.extend(pallet_tubes)
            self.logger.info(
                f"✓ Паллет П{pallet_id}: отсканировано {len(pallet_tubes)} пробирок"
            )
        
        if not all_scanned_tubes:
            self.logger.warning("Не найдено ни одной пробирки")
            return []
        
        self.logger.info(f"\n✓ Всего отсканировано: {len(all_scanned_tubes)} пробирок")
        
        # Параллельные запросы к ЛИС
        self.logger.info(f"Отправка {len(all_scanned_tubes)} запросов к ЛИС...")
        all_barcodes = [tube.barcode for tube in all_scanned_tubes]
        barcode_to_test_type = self.lis_client.get_tube_types_batch(all_barcodes)
        
        # Обновляем типы тестов
        for tube in all_scanned_tubes:
            tube.test_type = barcode_to_test_type.get(tube.barcode, TestType.ERROR)
            self.logger.debug(f"{tube.barcode} -> {tube.test_type.name}")
        
        # Добавляем в RackSystemManager
        for tube in all_scanned_tubes:
            self.rack_manager.add_scanned_tube(tube.source_rack, tube)
        
        # Статистика
        stats = {}
        for tube in all_scanned_tubes:
            stats[tube.test_type] = stats.get(tube.test_type, 0) + 1
        
        self.logger.info("\n📊 Статистика по типам тестов:")
        for test_type, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            self.logger.info(f"  {test_type.name}: {count} шт")
        
        self.logger.info(f"\n{'=' * 60}")
        self.logger.info("✓ СКАНИРОВАНИЕ ЗАВЕРШЕНО")
        self.logger.info(f"{'=' * 60}\n")
        
        return all_scanned_tubes

    # ==================== ФАЗА СОРТИРОВКИ ====================

    def _execute_sorting_iteration(self, tube: TubeInfo) -> bool:
        """
        Выполняет одну итерацию сортировки пробирки.

        ПРОТОКОЛ:
        1. Ждём R[1] = 0 (робот готов)
        2. Устанавливаем SR[2] = "SS TT DD RR"
        3. Устанавливаем SR[1] = "SORTING_ITERATION"
        4. Устанавливаем R[1] = 1 (запуск)
        5. Ждём R[1] = 2 (итерация завершена)
        6. Робот сам сбрасывает R[1] = 0

        Args:
            tube: Информация о пробирке

        Returns:
            True если успешно, False при ошибке
        """
        # Находим целевой штатив
        dest_rack = self.rack_manager.find_available_rack(tube.test_type)
        
        if not dest_rack:
            self.logger.error(f"Нет штативов для типа {tube.test_type.name}")
            return False

        dest_rack_id = dest_rack.rack_id
        dest_position = dest_rack.get_next_position()

        self.logger.info(
            f"Сортировка: {tube.barcode} ({tube.test_type.name}) "
            f"П{tube.source_rack}[{tube.number}] -> Штатив #{dest_rack_id}[{dest_position}]"
        )

        # 1. Ждём готовности робота
        if not self._wait_robot_ready(timeout=15.0):
            self.logger.error("Робот не готов для сортировки")
            return False

        # 2. Формируем данные: "SS TT DD RR"
        movement_data = (
            f"{tube.source_rack:02d} "
            f"{tube.number:02d} "
            f"{dest_rack_id:02d} "
            f"{dest_position:02d}"
        )
        self.robot.set_string_register(SR.movement_data, movement_data)
        self.logger.debug(f"SR[2: MOVEMENT_DATA] = '{movement_data}'")

        # 3. Устанавливаем тип итерации
        self.robot.set_string_register(SR.iteration_type, SR_VAL.sorting)
        self.logger.debug(f"SR[1: ITERATION_TYPE] = '{SR_VAL.sorting}'")

        # 4. ЗАПУСКАЕМ ИТЕРАЦИЮ
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.started)
        self.logger.debug("R[1] = 1 (итерация запущена)")

        # Робот сам сбрасывает R[1] = 0

        # Обновляем состояние
        dest_rack.add_tube(tube)
        self.rack_manager.mark_tube_sorted(tube.source_rack, tube.barcode)

        self.logger.info(
            f"✓ Пробирка размещена: Штатив #{dest_rack_id}[{tube.destination_number}] "
            f"({dest_rack.get_tube_count()}/{dest_rack.MAX_TUBES})"
        )

        return True

    def _sort_all_tubes(self, tubes: List[TubeInfo]) -> None:
        """
        ФАЗА 2: Физическая сортировка всех пробирок.
        """
        self.logger.info("\n" + "=" * 60)
        self.logger.info("ФАЗА 2: ФИЗИЧЕСКАЯ СОРТИРОВКА ПРОБИРОК")
        self.logger.info("=" * 60 + "\n")
        
        total = len(tubes)
        processed = 0
        failed = 0
        
        for idx, tube in enumerate(tubes, 1):
            if self.stop_event.is_set():
                break
            
            # Пропускаем ошибочные
            if tube.test_type in [TestType.ERROR, TestType.UNKNOWN]:
                self.logger.warning(f"Пропуск {tube.barcode} (тип: {tube.test_type.name})")
                failed += 1
                continue
            
            # Проверяем доступность штатива
            dest_rack = self.rack_manager.find_available_rack(tube.test_type)
            
            if not dest_rack:
                self.logger.warning(f"Нет штативов для {tube.test_type.name}")
                self._enter_waiting_mode(f"Заполнены штативы типа {tube.test_type.name}")
                
                if not self.operator_input.wait_for_rack_replacement():
                    if self.stop_event.is_set():
                        break
                    continue
                
                self._exit_waiting_mode()
                self.rack_manager.reset_rack_pair(tube.test_type)
                
                dest_rack = self.rack_manager.find_available_rack(tube.test_type)
                if not dest_rack:
                    self.logger.error(f"После замены нет штативов для {tube.test_type.name}")
                    failed += 1
                    continue
            
            # Выполняем сортировку
            if self._execute_sorting_iteration(tube):
                processed += 1
                if processed % 10 == 0 or processed == total:
                    self.logger.info(f"Прогресс: {processed}/{total} ({processed * 100 // total}%)")
            else:
                failed += 1
                self.logger.warning(f"✗ Ошибка сортировки {tube.barcode}")
            
            # Проверка паузы
            if not self.operator_input.check_pause():
                break
        
        self.logger.info(f"\n{'=' * 60}")
        self.logger.info("СОРТИРОВКА ЗАВЕРШЕНА")
        self.logger.info(f"Успешно: {processed}/{total}, Ошибок: {failed}")
        self.logger.info(f"{'=' * 60}\n")

    # ==================== РЕЖИМ ОЖИДАНИЯ ====================

    def _enter_waiting_mode(self, reason: str):
        """
        Переводит робота в режим ожидания (home позиция).
        """
        self.logger.warning(f"\n{'=' * 60}")
        self.logger.warning(f"⏸ РЕЖИМ ОЖИДАНИЯ")
        self.logger.warning(f"Причина: {reason}")
        self.logger.warning(f"{'=' * 60}\n")

        # 1. Ждём готовности
        if not self._wait_robot_ready(timeout=15.0):
            self.logger.warning("Робот не готов для паузы")
            return

        # 2. Устанавливаем тип итерации
        self.robot.set_string_register(SR.iteration_type, SR_VAL.pause)
        self.logger.debug(f"SR[1] = '{SR_VAL.pause}'")

        # 3. Запускаем
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.started)
        self.logger.debug("R[1] = 1")

        # 4. Ждём подтверждения (R[4] = 1)
        if not self._wait_until(
            lambda: self.robot.get_number_register(NR.pause_status) == NR_VAL.pause_ready,
            timeout=30.0
        ):
            self.logger.warning("Таймаут перехода в режим паузы")
            return

        self.logger.info("✓ Робот в режиме ожидания (home)")

    def _exit_waiting_mode(self):
        """
        Выход из режима ожидания.
        """
        self.logger.info("Выход из режима ожидания...")

        # 1. Сигнализируем роботу
        self.robot.set_number_register(NR.pause_status, NR_VAL.pause_not_ready)
        self.logger.debug("R[4] = 0")

        # 2. Ждём завершения
        if not self._wait_iteration_complete(timeout=10.0):
            self.logger.warning("Таймаут выхода из паузы")

        # Робот сам сбрасывает R[1] = 0

        self.logger.info("✓ Выход из режима ожидания завершён")

    # ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

    def _check_can_start_cycle(self) -> tuple[bool, str]:
        """Проверяет возможность начала цикла."""
        required_types = [TestType.UGI, TestType.VPCH, TestType.UGI_VPCH, TestType.OTHER]
        
        for test_type in required_types:
            if not self.rack_manager.has_available_rack(test_type):
                return False, f"Нет штативов для типа {test_type.name}"
        
        return True, ""
    
    def _get_system_status(self) -> str:
        """Статус системы для оператора."""
        lines = []
        
        lines.append("ИСХОДНЫЕ ПАЛЛЕТЫ:")
        for pallet in self.rack_manager.get_all_source_pallets():
            scanned = pallet.get_tube_count()
            sorted_count = pallet.get_sorted_count()
            lines.append(f"  П{pallet.pallet_id}: {scanned} скан., {sorted_count} сорт.")
        
        lines.append("\nЦЕЛЕВЫЕ ШТАТИВЫ:")
        for rack in self.rack_manager.get_all_destination_racks():
            count = rack.get_tube_count()
            status = rack.get_status().value
            lines.append(f"  #{rack.rack_id} ({rack.test_type.name}): {count}/50 [{status}]")
        
        return "\n".join(lines)

    # ==================== ГЛАВНЫЙ ЦИКЛ ====================

    def run(self) -> None:
        """Главный цикл работы робота."""
        self.logger.info("[Robot] Поток запущен")
        
        self.operator_input.start()
        
        try:
            # Подготовка робота
            self.logger.info("Подготовка робота...")
            self.robot.stop_all_running_programms()
            time.sleep(0.5)
            self.robot.reset_errors()
            time.sleep(0.5)
            self.robot.start_program(ROBOT_CFG.robot_program_name)
            time.sleep(1.0)
            self.logger.info("✓ Робот готов!")
            
            # Основной цикл
            while not self.stop_event.is_set():
                # 1. Проверка готовности
                can_start, reason = self._check_can_start_cycle()
                
                if not can_start:
                    self._enter_waiting_mode(reason)
                    if not self.operator_input.wait_for_rack_replacement():
                        continue
                    self._exit_waiting_mode()
                
                # 2. ФАЗА 1: Сканирование
                all_tubes = self._scan_all_source_racks()
                
                if self.stop_event.is_set():
                    break
                
                if not all_tubes:
                    self._enter_waiting_mode("Нет пробирок в штативах")
                    if not self.operator_input.wait_for_rack_replacement():
                        continue
                    self._exit_waiting_mode()
                    self.rack_manager.reset_all_source_pallets()
                    continue
                
                # 3. ФАЗА 2: Сортировка
                self._sort_all_tubes(all_tubes)
                
                if self.stop_event.is_set():
                    break
                
                # 4. Завершение цикла
                self.logger.info("\n" + "=" * 60)
                self.logger.info("✓ ЦИКЛ ЗАВЕРШЁН")
                self.logger.info("=" * 60 + "\n")
                
                self.rack_manager.clear_sorted_tubes()
                
                self._enter_waiting_mode("Требуется замена исходных штативов")
                
                if not self.operator_input.wait_for_rack_replacement():
                    continue
                
                self._exit_waiting_mode()
                self.rack_manager.reset_all_source_pallets()
        
        except Exception as e:
            self.logger.fatal(f"Критическая ошибка: {e}", exc_info=True)
        
        finally:
            self.operator_input.stop()
            self.logger.info("[Robot] Поток завершён")