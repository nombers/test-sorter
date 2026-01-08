# src/eppendorf_sorter/orchestration/robot_logic.py
"""
Главная логика сортировочного робота.
Отвечает за сканирование штативов и сортировку пробирок.

Процесс работы:
1. ФАЗА СКАНИРОВАНИЯ: Сканирование всех пробирок из двух исходных штативов (П0, П1)
   - Роботом сканируются все 100 позиций (2 штатива × 50 позиций)
   - Баркоды отправляются параллельно в ЛИС для определения типов тестов
   - Результаты сохраняются в RackSystemManager

2. ФАЗА СОРТИРОВКИ: Физическая сортировка пробирок в целевые штативы
   - Для каждой пробирки определяется целевой штатив на основе типа теста
   - Робот перемещает пробирку из исходного штатива в целевой
   - Целевые штативы заполняются последовательно

3. РЕЖИМ ОЖИДАНИЯ: Пауза при необходимости действий оператора
   - Когда исходные штативы пусты (нужна замена)
   - Когда все целевые штативы определённого типа заполнены (нужна замена)
"""
import time
import threading
import logging
from typing import List

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
    
    Цикл работы робота состоит из двух фаз:
    - Фаза 1: Полное сканирование исходных штативов с параллельными запросами к ЛИС
    - Фаза 2: Физическая сортировка всех отсканированных пробирок
    
    После завершения цикла робот переходит в режим ожидания замены штативов.
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
        """
        Args:
            rack_manager: Менеджер управления штативами
            robot: Робот-манипулятор
            scanner: QR-сканер для считывания баркодов
            lis_client: Клиент для параллельных запросов к ЛИС
            logger: Логгер для записи событий
            stop_event: Событие для остановки потока
        """
        super().__init__(name="RobotThread", daemon=True)
        self.rack_manager = rack_manager
        self.robot = robot
        self.scanner = scanner
        self.lis_client = lis_client
        self.logger = logger
        self.stop_event = stop_event
        
        # Обработчик команд оператора
        self.operator_input = OperatorInputHandler(logger, stop_event)
        self.operator_input.set_status_callback(self._get_system_status)

    def _wait_until(self, condition, poll: float = 0.1, timeout: float = 30.0) -> bool:
        """
        Ожидает выполнения условия с проверкой stop_event.

        Args:
            condition: Функция, возвращающая bool
            poll: Интервал проверки условия (сек)
            timeout: Максимальное время ожидания (сек)

        Returns:
            True если условие выполнилось, False если timeout или stop
        """
        start_time = time.time()
        while not self.stop_event.is_set():
            if condition():
                return True
            if time.time() - start_time > timeout:
                self.logger.warning(f"Таймаут ожидания условия ({timeout} сек)")
                return False
            time.sleep(poll)
        return False

    def _wait_robot_ready(self, timeout: float = 5.0) -> bool:
        """
        Ожидает готовности робота к принятию новой итерации.

        Args:
            timeout: Максимальное время ожидания (сек)

        Returns:
            True если робот готов, False если timeout или stop
        """
        return self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.ready,
            timeout=timeout
        )

    def _scan_position_group(
        self, pallet_id: int, row: int, col_start: int, col_end: int
    ) -> List[TubeInfo]:
        """
        Сканирует группу позиций в одной строке штатива.

        Одна итерация робота сканирует несколько позиций (атомарная операция).
        Сканер возвращает кортеж баркодов для всех позиций группы.

        Протокол:
        1. Ждём R[1: Iteration_starter] = 0 (робот готов)
        2. Устанавливаем SR[3: SCAN_DATA] = "PP NN"
        3. Устанавливаем SR[1: ITERATION_TYPE] = "SCANNING_ITERATION"
        4. Робот выполняет движение, сигнализирует через R[2: Scan_status]
        5. Производим сканирование
        6. Ждём R[1: Iteration_starter] = 2 (завершено)
        7. Сбрасываем R[1: Iteration_starter] = 0

        Args:
            pallet_id: ID паллета (0 или 1)
            row: Номер ряда (0-9)
            col_start: Начальная колонка (включительно)
            col_end: Конечная колонка (не включительно)

        Returns:
            Список TubeInfo для найденных пробирок
        """
        positions = [row * 5 + col for col in range(col_start, col_end)]
        group_size = len(positions)

        self.logger.debug(
            f"Сканирование П{pallet_id} ряд {row} колонки {col_start}-{col_end-1} "
            f"(позиции {positions})"
        )

        # 1. Ждём готовности робота
        if not self._wait_robot_ready(timeout=10.0):
            self.logger.warning(f"Робот не готов к сканированию П{pallet_id} ряд {row}")
            return []

        # 2. Формируем данные: "PP NN" - паллет и первая позиция группы
        first_position = positions[0]
        scan_data = f"{pallet_id:02d} {first_position:02d}"
        self.robot.set_string_register(SR.scan_data, scan_data)
        self.logger.debug(f"SR[3: SCAN_DATA] = '{scan_data}'")

        # 3. Устанавливаем тип итерации - сканирование
        self.robot.set_string_register(SR.iteration_type, SR_VAL.scanning)
        self.logger.debug(f"SR[1: ITERATION_TYPE] = '{SR_VAL.scanning}'")

        # 4. Ожидаем, что робот переместился и готов к сканированию (R[2: Scan_status] != 0)
        if not self._wait_until(
            lambda: self.robot.get_number_register(NR.scan_status) != NR_VAL.scan_reset,
            timeout=15.0
        ):
            self.logger.warning(f"Таймаут ожидания готовности к сканированию П{pallet_id} ряд {row}")
            # Сбрасываем состояние
            self.robot.set_string_register(SR.iteration_type, SR_VAL.none)
            return []

        self.logger.debug("Робот в позиции сканирования")

        # 5. Сканируем группу позиций - возвращает кортеж из 2 или 3 баркодов
        barcodes = self.scanner.scan(timeout=SCANNER_CFG.timeout)

        # Проверяем что получили правильное количество баркодов
        if len(barcodes) != group_size:
            self.logger.warning(
                f"Ожидалось {group_size} баркодов, получено {len(barcodes)}"
            )

        # Обрабатываем результаты сканирования
        tubes: List[TubeInfo] = []
        has_valid_barcode = False

        for i, barcode in enumerate(barcodes):
            if i >= group_size:
                break
            position = positions[i]

            if barcode and barcode != "NoRead":
                has_valid_barcode = True
                self.logger.debug(f"П{pallet_id}[{position}] -> {barcode}")

                tube = TubeInfo(
                    barcode=barcode,
                    source_rack=pallet_id,
                    number=position,
                    test_type=TestType.UNKNOWN
                )
                tubes.append(tube)
            else:
                self.logger.debug(f"П{pallet_id}[{position}] - пусто")

        # Сообщаем роботу результат сканирования
        if has_valid_barcode:
            self.robot.set_number_register(NR.scan_status, NR_VAL.scan_good)
        else:
            self.robot.set_number_register(NR.scan_status, NR_VAL.scan_bad)

        # 6. Ожидаем завершения итерации (R[1: Iteration_starter] = 2)
        if not self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed,
            timeout=15.0
        ):
            self.logger.warning(f"Таймаут завершения итерации П{pallet_id} ряд {row}")

        # 7. Сбрасываем регистры для следующей итерации
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)
        self.robot.set_number_register(NR.scan_status, NR_VAL.scan_reset)
        self.robot.set_string_register(SR.iteration_type, SR_VAL.none)

        return tubes

    def _scan_all_source_racks(self) -> List[TubeInfo]:
        """
        ФАЗА 1: Сканирование всех пробирок из исходных штативов.
        
        Использует протокол взаимодействия с роботом через регистры:
        - Отправляет данные о позиции для сканирования через String Register
        - Робот сам позиционируется к нужной позиции
        - Получает команду на сканирование
        - Сканер считывает QR код
        - Отправляет результат роботу
        
        Returns:
            Список TubeInfo со всей информацией о пробирках
        """
        self.logger.info("\n" + "="*60)
        self.logger.info("ФАЗА 1: СКАНИРОВАНИЕ ИСХОДНЫХ ШТАТИВОВ")
        self.logger.info("="*60 + "\n")
        
        all_scanned_tubes: List[TubeInfo] = []
        
        # Получаем все исходные штативы (П0, П1)
        source_pallets = self.rack_manager.get_all_source_pallets()
        
        for pallet in source_pallets:
            if self.stop_event.is_set():
                return []
            
            # Используем pallet_id (алиас для rack_id)
            self.logger.info(f"Сканирование паллета П{pallet.pallet_id}...")
            
            # Занимаем паллет для сканирования
            pallet.occupy()
            
            try:
                # Сканируем 10 рядов × 5 колонок
                # Порядок: сначала колонки 0-2 (3 шт), потом колонки 3-4 (2 шт)
                for row in range(10):
                    if self.stop_event.is_set():
                        break

                    # Группа 1: колонки 0, 1, 2 (3 пробирки)
                    tubes_group1 = self._scan_position_group(
                        pallet.pallet_id, row, col_start=0, col_end=3
                    )
                    all_scanned_tubes.extend(tubes_group1)

                    # Проверка паузы после атомарной операции
                    if not self.operator_input.check_pause():
                        break

                    # Группа 2: колонки 3, 4 (2 пробирки)
                    tubes_group2 = self._scan_position_group(
                        pallet.pallet_id, row, col_start=3, col_end=5
                    )
                    all_scanned_tubes.extend(tubes_group2)

                    # Проверка паузы после атомарной операции
                    if not self.operator_input.check_pause():
                        break
            
            finally:
                pallet.release()
            
            scanned_count = len([t for t in all_scanned_tubes if t.source_rack == pallet.pallet_id])
            self.logger.info(f"✓ Паллет П{pallet.pallet_id}: отсканировано {scanned_count} пробирок")
        
        if not all_scanned_tubes:
            self.logger.warning("Не найдено ни одной пробирки для сортировки")
            return []
        
        self.logger.info(f"\n✓ Всего отсканировано: {len(all_scanned_tubes)} пробирок")
        
        # Параллельные запросы к ЛИС для определения типов тестов
        self.logger.info(f"Отправка {len(all_scanned_tubes)} запросов к ЛИС...")
        all_barcodes = [tube.barcode for tube in all_scanned_tubes]
        barcode_to_test_type = self.lis_client.get_tube_types_batch(all_barcodes)
        
        # Обновляем информацию о типах тестов
        for tube in all_scanned_tubes:
            test_type = barcode_to_test_type.get(tube.barcode, TestType.ERROR)
            tube.test_type = test_type
            self.logger.debug(f"{tube.barcode} -> {test_type.name}")
        
        # Добавляем отсканированные пробирки в RackSystemManager
        for tube in all_scanned_tubes:
            self.rack_manager.add_scanned_tube(tube.source_rack, tube)
        
        # Выводим статистику по типам тестов
        stats = {}
        for tube in all_scanned_tubes:
            stats[tube.test_type] = stats.get(tube.test_type, 0) + 1
        
        self.logger.info("\n📊 Статистика по типам тестов:")
        for test_type, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            self.logger.info(f"  {test_type.name}: {count} шт")
        
        self.logger.info(f"\n{'='*60}")
        self.logger.info("✓ СКАНИРОВАНИЕ ЗАВЕРШЕНО")
        self.logger.info(f"{'='*60}\n")
        
        return all_scanned_tubes

    def _execute_sorting_iteration(self, tube: TubeInfo) -> bool:
        """
        Выполняет одну итерацию сортировки пробирки.

        Протокол:
        1. Ждём R[1: Iteration_starter] = 0 (робот готов)
        2. Устанавливаем SR[2: MOVEMENT_DATA] = "SS TT DD RR"
        3. Устанавливаем SR[1: ITERATION_TYPE] = "SORTING_ITERATION"
        4. Робот выполняет перемещение пробирки
        5. Ждём R[1: Iteration_starter] = 2 (завершено)
        6. Сбрасываем R[1: Iteration_starter] = 0

        Args:
            tube: Информация о пробирке

        Returns:
            True если пробирка успешно отсортирована, False при ошибке
        """
        self.logger.info(f"\n====SORTING ITERATION====")
        self.logger.info(f"Пробирка: {tube.barcode} ({tube.test_type.name})")

        # Находим доступный целевой штатив для данного типа теста
        dest_rack = self.rack_manager.find_available_rack(tube.test_type)

        if not dest_rack:
            self.logger.error(f"Нет доступных штативов для типа {tube.test_type.name}")
            return False

        # Определяем ID штатива и следующую свободную позицию
        dest_rack_id = dest_rack.rack_id
        dest_position = dest_rack.get_next_position()

        self.logger.info(f"Маршрут: П{tube.source_rack}[{tube.number}] -> Штатив #{dest_rack_id}[{dest_position}]")

        # 1. Ждём готовности робота
        if not self._wait_robot_ready(timeout=10.0):
            self.logger.error("Робот не готов к сортировке")
            return False

        # 2. Формируем строку данных для робота: "SS TT DD RR"
        data_str = (
            f"{tube.source_rack:02d} "
            f"{tube.number:02d} "
            f"{dest_rack_id:02d} "
            f"{dest_position:02d}"
        )

        # Отправляем данные роботу через String Register
        self.robot.set_string_register(SR.movement_data, data_str)
        self.logger.info(f"SR[2: MOVEMENT_DATA] = '{data_str}'")

        # 3. Устанавливаем тип итерации - сортировка
        self.robot.set_string_register(SR.iteration_type, SR_VAL.sorting)
        self.logger.info(f"SR[1: ITERATION_TYPE] = '{SR_VAL.sorting}'")

        # 4. Ожидаем завершения итерации (R[1: Iteration_starter] = 2)
        self.logger.info("Ожидание завершения перемещения...")
        if not self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed,
            timeout=60.0  # Увеличен таймаут для физического перемещения
        ):
            self.logger.error("Таймаут ожидания завершения итерации")
            # Сбрасываем состояние
            self.robot.set_string_register(SR.iteration_type, SR_VAL.none)
            return False

        self.logger.info("✓ Итерация завершена")

        # Обновляем информацию в RackSystemManager
        # add_tube() сам устанавливает destination_rack и destination_number
        dest_rack.add_tube(tube)
        self.rack_manager.mark_tube_sorted(tube.source_rack, tube.barcode)

        self.logger.info(f"✓ Пробирка размещена: Штатив #{dest_rack_id}[{tube.destination_number}]")

        # 5. Сбрасываем регистры для следующей итерации
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)
        self.robot.set_string_register(SR.iteration_type, SR_VAL.none)

        # Логируем текущее состояние целевого штатива
        self.logger.info(f"Штатив #{dest_rack_id}: {dest_rack.get_tube_count()}/{dest_rack.MAX_TUBES} пробирок")

        return True

    def _sort_all_tubes(self, tubes: List[TubeInfo]) -> None:
        """
        ФАЗА 2: Физическая сортировка всех отсканированных пробирок.
        
        Args:
            tubes: Список пробирок для сортировки
        """
        self.logger.info("\n" + "="*60)
        self.logger.info("ФАЗА 2: ФИЗИЧЕСКАЯ СОРТИРОВКА ПРОБИРОК")
        self.logger.info("="*60 + "\n")
        
        total_tubes = len(tubes)
        processed = 0
        failed = 0
        
        for idx, tube in enumerate(tubes, 1):
            if self.stop_event.is_set():
                break
            
            self.logger.info(f"\n[{idx}/{total_tubes}] Обработка пробирки {tube.barcode}")
            
            # Пропускаем пробирки с ошибочным или неизвестным типом
            if tube.test_type in [TestType.ERROR, TestType.UNKNOWN]:
                self.logger.warning(f"Пропуск пробирки {tube.barcode} (тип: {tube.test_type.name})")
                failed += 1
                continue
            
            # Проверяем доступность целевого штатива для данного типа теста
            dest_rack = self.rack_manager.find_available_rack(tube.test_type)
            
            if not dest_rack:
                # Нет доступных штативов - ждём замены от оператора
                self.logger.error(f"Нет доступных штативов для типа {tube.test_type.name}")
                self._enter_waiting_mode(f"Заполнены все штативы типа {tube.test_type.name}")

                # Ожидаем подтверждения от оператора
                if not self.operator_input.wait_for_rack_replacement():
                    # Timeout или stop_event
                    if self.stop_event.is_set():
                        break
                    continue

                # Выходим из режима паузы
                self._exit_waiting_mode()

                # Сбрасываем штативы этого типа (оператор заменил их)
                self.rack_manager.reset_rack_pair(tube.test_type)

                # Проверяем ещё раз после замены
                dest_rack = self.rack_manager.find_available_rack(tube.test_type)
                if not dest_rack:
                    self.logger.error(f"После замены всё ещё нет штативов для {tube.test_type.name}")
                    continue

                self.logger.info(f"✓ Доступен штатив #{dest_rack.rack_id} для типа {tube.test_type.name}")
            
            # Выполняем сортировку пробирки
            success = self._execute_sorting_iteration(tube)

            if success:
                processed += 1
                progress_pct = (processed * 100) // total_tubes
                self.logger.info(f"Прогресс: {processed}/{total_tubes} ({progress_pct}%)")
            else:
                failed += 1
                self.logger.warning(f"✗ Не удалось обработать пробирку {tube.barcode}")

            # Проверка паузы после атомарной операции (сортировка одной пробирки)
            if not self.operator_input.check_pause():
                break
        
        # Итоговая статистика фазы сортировки
        self.logger.info(f"\n{'='*60}")
        self.logger.info("ФАЗА СОРТИРОВКИ ЗАВЕРШЕНА")
        self.logger.info(f"Успешно обработано: {processed}/{total_tubes}")
        self.logger.info(f"Ошибок: {failed}")
        self.logger.info(f"{'='*60}\n")

    def _enter_waiting_mode(self, reason: str):
        """
        Переводит робота в режим ожидания (пауза).

        Протокол:
        1. Ждём R[1: Iteration_starter] = 0 (робот готов)
        2. Устанавливаем SR[1: ITERATION_TYPE] = "PAUSE"
        3. Робот перемещается в home позицию
        4. Ждём R[4: Pause_status] = 1 (пауза выполнена, оператор может действовать)
        5. После действий оператора сбрасываем R[4: Pause_status] = 0
        6. Устанавливаем SR[1: ITERATION_TYPE] = "NONE"

        Args:
            reason: Причина перехода в режим ожидания
        """
        self.logger.warning(f"\n{'='*60}")
        self.logger.warning(f"⏸ РЕЖИМ ОЖИДАНИЯ")
        self.logger.warning(f"Причина: {reason}")
        self.logger.warning(f"Требуется действие оператора")
        self.logger.warning(f"{'='*60}\n")

        # Ждём готовности робота
        if not self._wait_robot_ready(timeout=10.0):
            self.logger.warning("Робот не готов к переходу в режим паузы")
            return

        # Устанавливаем тип итерации PAUSE
        self.robot.set_string_register(SR.iteration_type, SR_VAL.pause)
        self.logger.info(f"SR[1: ITERATION_TYPE] = '{SR_VAL.pause}'")

        # Ждём подтверждения что робот в home позиции (R[4: Pause_status] = 1)
        if not self._wait_until(
            lambda: self.robot.get_number_register(NR.pause_status) == NR_VAL.pause_ready,
            timeout=30.0
        ):
            self.logger.warning("Таймаут ожидания перехода в режим паузы")
            return

        self.logger.info("✓ Робот в режиме ожидания (home позиция)")

    def _exit_waiting_mode(self):
        """
        Выход из режима ожидания после действий оператора.

        Протокол:
        1. Сбрасываем R[4: Pause_status] = 0
        2. Ждём R[1: Iteration_starter] = 2 (пауза завершена)
        3. Сбрасываем R[1: Iteration_starter] = 0
        4. Устанавливаем SR[1: ITERATION_TYPE] = "NONE"
        """
        self.logger.info("Выход из режима ожидания...")

        # Сбрасываем статус паузы
        self.robot.set_number_register(NR.pause_status, NR_VAL.pause_not_ready)

        # Ждём завершения паузы (R[1: Iteration_starter] = 2)
        if not self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed,
            timeout=10.0
        ):
            self.logger.warning("Таймаут ожидания завершения паузы")

        # Сбрасываем регистры
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)
        self.robot.set_string_register(SR.iteration_type, SR_VAL.none)

        self.logger.info("✓ Выход из режима ожидания завершён")

    def _check_can_start_cycle(self) -> tuple[bool, str]:
        """
        Проверяет возможность начала нового цикла работы.
        
        Returns:
            (can_start, reason) - кортеж (можно ли начать цикл, причина если нельзя)
        """
        required_types = [TestType.UGI, TestType.VPCH, TestType.UGI_VPCH, TestType.OTHER]
        
        for test_type in required_types:
            if not self.rack_manager.has_available_rack(test_type):
                return False, f"Нет доступных штативов для типа {test_type.name}"
        
        return True, ""
    
    def _get_system_status(self) -> str:
        """Получить текстовый статус системы для отображения оператору."""
        lines = []
        
        # Статус исходных паллетов
        lines.append("ИСХОДНЫЕ ПАЛЛЕТЫ:")
        for pallet in self.rack_manager.get_all_source_pallets():
            scanned = pallet.get_tube_count()
            sorted_count = pallet.get_sorted_count()
            lines.append(f"  П{pallet.pallet_id}: {scanned} отсканировано, {sorted_count} отсортировано")
        
        # Статус целевых штативов
        lines.append("\nЦЕЛЕВЫЕ ШТАТИВЫ:")
        for rack in self.rack_manager.get_all_destination_racks():
            count = rack.get_tube_count()
            status = rack.get_status().value
            lines.append(f"  #{rack.rack_id} ({rack.test_type.name}): {count}/50 [{status}]")
        
        return "\n".join(lines)

    def run(self) -> None:
        """
        Главный цикл работы робота.
        """
        self.logger.info("[Robot] Поток запущен")
        
        # Запускаем обработчик команд оператора
        self.operator_input.start()
        
        try:
            # Подготовка робота к работе
            self.logger.info("Подготовка робота...")
            self.robot.stop_all_running_programms()
            time.sleep(0.5)
            self.robot.reset_errors()
            time.sleep(0.5)
            self.robot.start_program(ROBOT_CFG.robot_program_name)
            time.sleep(1.0)
            self.logger.info("✓ Робот готов к работе!")
            
            # Основной цикл работы
            while not self.stop_event.is_set():
                # 1. Проверяем возможность начала нового цикла
                can_start, reason = self._check_can_start_cycle()
                
                if not can_start:
                    self._enter_waiting_mode(reason)
                    if not self.operator_input.wait_for_rack_replacement():
                        continue
                    self._exit_waiting_mode()
                
                # 2. ФАЗА 1: Сканирование всех исходных штативов
                all_tubes = self._scan_all_source_racks()
                
                if self.stop_event.is_set():
                    break
                
                if not all_tubes:
                    self._enter_waiting_mode("Нет пробирок в исходных штативах")
                    if not self.operator_input.wait_for_rack_replacement():
                        continue
                    self._exit_waiting_mode()
                    # После замены сбрасываем паллеты
                    self.rack_manager.reset_all_source_pallets()
                    continue
                
                # 3. ФАЗА 2: Физическая сортировка всех пробирок
                self._sort_all_tubes(all_tubes)
                
                if self.stop_event.is_set():
                    break
                
                # 4. Завершение цикла
                self.logger.info("\n" + "="*60)
                self.logger.info("✓ ЦИКЛ РАБОТЫ ЗАВЕРШЁН")
                self.logger.info("Исходные штативы обработаны")
                self.logger.info("="*60 + "\n")
                
                # Очищаем данные о отсортированных пробирках
                self.rack_manager.clear_sorted_tubes()
                
                # Переходим в режим ожидания замены исходных штативов
                self._enter_waiting_mode("Цикл завершён - требуется замена исходных штативов")

                # Ожидаем подтверждения от оператора
                if not self.operator_input.wait_for_rack_replacement():
                    continue

                self._exit_waiting_mode()

                # Сбрасываем исходные паллеты для нового цикла
                self.rack_manager.reset_all_source_pallets()
        
        except Exception as e:
            self.logger.fatal(f"Критическая ошибка в главном потоке: {e}", exc_info=True)
        
        finally:
            self.operator_input.stop()
            self.logger.info("[Robot] Поток завершён")