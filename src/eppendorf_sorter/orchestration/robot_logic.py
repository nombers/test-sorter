# src/eppendorf_sorter/orchestration/robot_logic.py
"""Главная логика сортировочного робота с параллельной обработкой.

АРХИТЕКТУРА (3 параллельных потока):
====================================

1. ScannerThread (в RobotThread) - Сканирование пробирок
   - Сканирует группы пробирок (3+2 за итерацию)
   - Парсит баркоды из строки "barcode1;barcode2;barcode3"
   - Создаёт объекты TubeInfo с test_type=UNKNOWN
   - Кладёт баркоды в barcode_queue для запросов к ЛИС
   - Сигнализирует scanning_complete по завершении

2. LISRequestThread - Запросы к ЛИС
   - Забирает баркоды из barcode_queue
   - Отправляет HTTP запросы к ЛИС (параллельно через ThreadPoolExecutor)
   - Обновляет TubeInfo.test_type
   - Кладёт готовые пробирки в ready_to_sort_queue
   - Сигнализирует lis_complete по завершении

3. RobotThread - Основной поток
   - Выполняет сканирование (синхронизация с роботом)
   - Ждёт scanning_complete
   - Сортирует пробирки из ready_to_sort_queue (FIFO)
   - Обрабатывает паузы и ожидание замены штативов

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
"""
import time
import threading
import logging
from queue import Queue, Empty
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.eppendorf_sorter.devices import CellRobot, Scanner
from src.eppendorf_sorter.config.robot_config import load_robot_config
from src.eppendorf_sorter.domain.racks import (
    RackSystemManager,
    TestType,
    TubeInfo,
)
from src.eppendorf_sorter.lis import LISClient
from src.eppendorf_sorter.lis.client import get_tube_info_sync, parse_test_type
from .robot_protocol import NR, NR_VAL, SR, SR_VAL


ROBOT_CFG = load_robot_config()
SCANNER_CFG = ROBOT_CFG.scanner
LIS_CFG = ROBOT_CFG.lis


# ==================== СТРУКТУРЫ ДАННЫХ ====================

@dataclass
class ScanResult:
    """Результат сканирования одной пробирки для передачи между потоками.

    Attributes:
        tube: Информация о пробирке (баркод, позиция, тип теста).
        scan_time: Время выполнения сканирования в секундах.
    """
    tube: TubeInfo
    scan_time: float = 0.0


@dataclass
class PipelineContext:
    """Контекст для межпоточного взаимодействия между RobotThread и LISRequestThread.

    Содержит очереди для передачи данных, события синхронизации,
    счётчики статистики и общий список отсканированных пробирок.
    Все поля потокобезопасны (защищены Lock или используют Queue).

    Attributes:
        barcode_queue: Очередь ScanResult с пробирками для отправки в ЛИС.
        ready_to_sort_queue: Очередь TubeInfo с пробирками, готовыми к сортировке.
        scanning_complete: Событие завершения фазы сканирования.
        lis_complete: Событие завершения всех запросов к ЛИС.
        stop_event: Глобальное событие остановки системы.
        pause_event: Событие паузы для приостановки обработки.
        sent_to_lis: Счётчик отправленных запросов в ЛИС.
        received_from_lis: Счётчик полученных ответов от ЛИС.
        counter_lock: Lock для потокобезопасного доступа к счётчикам.
        all_scanned_tubes: Полный список всех отсканированных пробирок.
        tubes_lock: Lock для потокобезопасного доступа к списку пробирок.
    """
    # Очередь баркодов для отправки в ЛИС
    # Элементы: ScanResult (пробирка с UNKNOWN типом)
    barcode_queue: Queue = field(default_factory=Queue)

    # Очередь пробирок готовых к сортировке (ответ от ЛИС получен)
    ready_to_sort_queue: Queue = field(default_factory=Queue)

    # Событие: сканирование завершено
    scanning_complete: threading.Event = field(default_factory=threading.Event)

    # Событие: все ответы от ЛИС получены
    lis_complete: threading.Event = field(default_factory=threading.Event)

    # Глобальное событие остановки
    stop_event: threading.Event = field(default_factory=threading.Event)

    # Событие паузы (сканирование и ЛИС должны остановиться)
    pause_event: threading.Event = field(default_factory=threading.Event)

    # Счётчик отправленных в ЛИС
    sent_to_lis: int = 0

    # Счётчик полученных от ЛИС
    received_from_lis: int = 0

    # Lock для счётчиков
    counter_lock: threading.Lock = field(default_factory=threading.Lock)

    # Все отсканированные пробирки (для статистики и RackManager)
    all_scanned_tubes: List[TubeInfo] = field(default_factory=list)
    tubes_lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self):
        """Сбрасывает контекст для нового цикла сканирования/сортировки.

        Очищает очереди, сбрасывает события, обнуляет счётчики
        и удаляет список пробирок.
        """
        # --- Очистка очередей ---
        while not self.barcode_queue.empty():
            try:
                self.barcode_queue.get_nowait()
            except Empty:
                break

        while not self.ready_to_sort_queue.empty():
            try:
                self.ready_to_sort_queue.get_nowait()
            except Empty:
                break

        # --- Сброс событий ---
        self.scanning_complete.clear()
        self.lis_complete.clear()
        self.pause_event.clear()

        # --- Сброс счётчиков ---
        with self.counter_lock:
            self.sent_to_lis = 0
            self.received_from_lis = 0

        # --- Очистка списка пробирок ---
        with self.tubes_lock:
            self.all_scanned_tubes = []

    def increment_sent(self):
        """Потокобезопасно увеличивает счётчик отправленных запросов в ЛИС."""
        with self.counter_lock:
            self.sent_to_lis += 1

    def increment_received(self):
        """Потокобезопасно увеличивает счётчик полученных ответов от ЛИС."""
        with self.counter_lock:
            self.received_from_lis += 1

    def add_scanned_tube(self, tube: TubeInfo):
        """Потокобезопасно добавляет отсканированную пробирку в общий список.

        Args:
            tube: Информация о пробирке для добавления.
        """
        with self.tubes_lock:
            self.all_scanned_tubes.append(tube)

    def get_all_scanned_tubes(self) -> List[TubeInfo]:
        """Возвращает потокобезопасную копию списка всех отсканированных пробирок.

        Returns:
            Копия списка TubeInfo на момент вызова.
        """
        with self.tubes_lock:
            return self.all_scanned_tubes.copy()


# ==================== ПОТОК ЗАПРОСОВ К ЛИС ====================

class LISRequestThread(threading.Thread):
    """Поток для асинхронной отправки запросов к ЛИС.

    Забирает баркоды из ``barcode_queue``, отправляет запросы к ЛИС
    через ThreadPoolExecutor, обновляет TubeInfo.test_type
    и кладёт готовые пробирки в ``ready_to_sort_queue``.

    Работает бесконечно до stop_event, обрабатывая несколько циклов
    сканирования. После каждого цикла (когда scanning_complete
    и все запросы обработаны) устанавливает lis_complete.

    Attributes:
        context: Общий контекст межпоточного взаимодействия.
        lis_host: IP-адрес сервера ЛИС.
        lis_port: Порт сервера ЛИС.
        logger: Логгер для записи событий потока.
        max_workers: Максимальное число параллельных запросов к ЛИС.
        timeout: Таймаут одного запроса к ЛИС в секундах.
        executor: Пул потоков для параллельного выполнения запросов.
    """

    def __init__(
        self,
        context: PipelineContext,
        lis_host: str,
        lis_port: int,
        logger: logging.Logger,
        max_workers: int = 20,
        timeout: float = 60.0,
    ):
        """Инициализирует поток запросов к ЛИС.

        Args:
            context: Общий контекст с очередями и событиями синхронизации.
            lis_host: IP-адрес сервера ЛИС.
            lis_port: Порт сервера ЛИС.
            logger: Логгер для записи событий.
            max_workers: Максимальное число параллельных запросов.
            timeout: Таймаут одного запроса к ЛИС в секундах.
        """
        super().__init__(name="LISRequestThread", daemon=True)
        self.context = context
        self.lis_host = lis_host
        self.lis_port = lis_port
        self.logger = logger
        self.max_workers = max_workers
        self.timeout = timeout
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def run(self):
        """Основной цикл потока: обработка очереди баркодов до stop_event.

        Цикл непрерывно:
        1. Проверяет паузу.
        2. Берёт баркод из barcode_queue и отправляет запрос в executor.
        3. Собирает завершённые future и кладёт результаты в ready_to_sort_queue.
        4. При scanning_complete и пустой очереди устанавливает lis_complete.
        """
        self.logger.info("[LIS] Поток запущен")

        pending_futures: Dict = {}  # future -> ScanResult

        try:
            while not self.context.stop_event.is_set():
                # --- Проверка паузы ---
                if self.context.pause_event.is_set():
                    time.sleep(0.1)
                    continue

                # --- Получение нового баркода из очереди ---
                try:
                    scan_result: ScanResult = self.context.barcode_queue.get(timeout=0.1)

                    # Отправляем запрос в executor
                    future = self.executor.submit(
                        get_tube_info_sync,
                        scan_result.tube.barcode,
                        self.lis_host,
                        self.lis_port,
                        self.timeout
                    )
                    pending_futures[future] = scan_result
                    self.context.increment_sent()
                    self.logger.debug(f"[LIS] Отправлен запрос для {scan_result.tube.barcode}")

                except Empty:
                    pass

                # --- Обработка завершённых запросов ---
                completed = []
                for future in pending_futures:
                    if future.done():
                        completed.append(future)

                for future in completed:
                    scan_result = pending_futures.pop(future)
                    try:
                        response = future.result()
                        test_type, raw_tests = parse_test_type(response)
                    except Exception as e:
                        self.logger.error(f"[LIS] Ошибка запроса для {scan_result.tube.barcode}: {e}")
                        test_type = TestType.ERROR
                        raw_tests = []

                    # Обновляем тип теста и сырые тесты
                    scan_result.tube.test_type = test_type
                    scan_result.tube.raw_tests = raw_tests
                    self.context.increment_received()

                    # Кладём в очередь готовых к сортировке
                    self.context.ready_to_sort_queue.put(scan_result.tube)
                    self.logger.info(f"[LIS] {scan_result.tube.barcode} -> {test_type.name} (raw: {raw_tests})")

                # --- Проверка завершения цикла ---
                if self.context.scanning_complete.is_set():
                    # Сканирование завершено, ждём завершения всех pending запросов
                    if not pending_futures and self.context.barcode_queue.empty():
                        # Цикл завершён - сигнализируем и ждём следующий
                        if not self.context.lis_complete.is_set():
                            self.context.lis_complete.set()
                            self.logger.info("[LIS] Цикл завершён, ожидание следующего цикла...")

            # --- Завершение: дожидаемся pending запросов при остановке ---
            if pending_futures:
                self.logger.info(f"[LIS] Завершение {len(pending_futures)} pending запросов...")
                for future in as_completed(pending_futures.keys()):
                    scan_result = pending_futures[future]
                    try:
                        response = future.result(timeout=5.0)
                        test_type, raw_tests = parse_test_type(response)
                    except Exception as e:
                        self.logger.error(f"[LIS] Ошибка запроса для {scan_result.tube.barcode}: {e}")
                        test_type = TestType.ERROR
                        raw_tests = []

                    scan_result.tube.test_type = test_type
                    scan_result.tube.raw_tests = raw_tests
                    self.context.increment_received()
                    self.context.ready_to_sort_queue.put(scan_result.tube)

        except Exception as e:
            self.logger.error(f"[LIS] Критическая ошибка: {e}", exc_info=True)

        finally:
            self.context.lis_complete.set()
            self.logger.info("[LIS] Поток завершён")

    def shutdown(self):
        """Корректно завершает работу пула потоков executor."""
        self.executor.shutdown(wait=False)


# ==================== ОСНОВНОЙ ПОТОК РОБОТА ====================

class RobotThread(threading.Thread):
    """Основной поток управления роботом-сортировщиком.

    Координирует полный цикл работы: сканирование всех паллетов,
    параллельные запросы к ЛИС (через LISRequestThread) и физическую
    сортировку пробирок по целевым штативам.

    Поддерживает управление из GUI: пауза, продолжение, остановка
    с уходом в home, замена штативов.

    Attributes:
        rack_manager: Менеджер исходных и целевых штативов.
        robot: Интерфейс управления физическим роботом.
        scanner: Интерфейс управления сканером баркодов.
        lis_client: Клиент для запросов к ЛИС (для совместимости).
        logger: Логгер для записи событий.
        stop_event: Глобальное событие остановки.
        context: Общий контекст межпоточного взаимодействия.
        lis_thread: Поток запросов к ЛИС.
    """

    def __init__(
        self,
        rack_manager: RackSystemManager,
        robot: CellRobot,
        scanner: Scanner,
        lis_client: LISClient,
        context: PipelineContext,
        lis_thread: LISRequestThread,
        logger: logging.Logger,
        stop_event: threading.Event,
    ) -> None:
        """Инициализирует поток робота со всеми зависимостями.

        Args:
            rack_manager: Менеджер исходных и целевых штативов.
            robot: Интерфейс управления физическим роботом.
            scanner: Интерфейс управления сканером баркодов.
            lis_client: Клиент для запросов к ЛИС.
            context: Общий контекст с очередями и событиями синхронизации.
            lis_thread: Поток запросов к ЛИС (должен быть запущен отдельно).
            logger: Логгер для записи событий.
            stop_event: Глобальное событие остановки.
        """
        super().__init__(name="RobotThread", daemon=True)
        self.rack_manager = rack_manager
        self.robot = robot
        self.scanner = scanner
        self.lis_client = lis_client
        self.logger = logger
        self.stop_event = stop_event

        # Контекст для межпоточного взаимодействия (инъекция из bootstrap)
        self.context = context

        # Поток запросов к ЛИС (инъекция из bootstrap)
        self.lis_thread = lis_thread

        # События для управления из GUI
        self._pause_requested = threading.Event()  # Запрос на паузу
        self._resume_event = threading.Event()     # Сигнал продолжения после паузы
        self._rack_replaced_event = threading.Event()  # Сигнал замены штатива
        self._stop_requested = threading.Event()   # Запрос на остановку (с уходом в home)
        self._in_waiting_mode = False  # Флаг: робот в режиме ожидания

    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================

    def _wait_until(self, condition, poll: float = 0.05) -> bool:
        """Ожидает выполнения условия с проверкой stop_event.

        Блокирует поток до тех пор, пока ``condition()`` не вернёт True
        или не будет установлен stop_event.

        Args:
            condition: Вызываемый объект без аргументов, возвращающий bool.
            poll: Интервал опроса в секундах между проверками.

        Returns:
            True если условие выполнено, False если получен stop_event.
        """
        while not self.stop_event.is_set():
            try:
                if condition():
                    return True
            except Exception as e:
                self.logger.warning(f"Ошибка при проверке условия: {e}")

            # Пауза между проверками
            time.sleep(poll)

        return False

    def _wait_robot_ready(self) -> bool:
        """Ожидает готовности робота (R[1] = 0).

        Returns:
            True если робот готов, False если получен stop_event.
        """
        result = self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.ready
        )
        return result

    def _wait_scan_ready(self) -> bool:
        """Ожидает готовности робота к сканированию (R[2] = 1).

        Returns:
            True если робот в позиции сканирования, False если получен stop_event.
        """
        self.logger.debug("Ожидание позиционирования (R[2] = 1)...")
        result = self._wait_until(
            lambda: self.robot.get_number_register(NR.scan_status) == NR_VAL.scan_good
        )
        if result:
            self.logger.debug("Робот в позиции сканирования")
        return result

    def _wait_iteration_complete(self) -> bool:
        """Ожидает завершения итерации (R[1] = 2).

        Returns:
            True если итерация завершена, False если получен stop_event.
        """
        self.logger.debug("Ожидание завершения итерации (R[1] = 2)...")
        result = self._wait_until(
            lambda: self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed
        )
        if result:
            self.logger.debug("Итерация завершена")
        return result

    def _wait_scan_ready_no_stop(self, timeout: float = 30.0) -> bool:
        """Ожидает готовности робота к сканированию (R[2] = 1) без проверки stop_event.

        Используется при завершении работы, когда нужно дождаться
        окончания движения робота перед корректным сбросом регистров.

        Args:
            timeout: Максимальное время ожидания в секундах.

        Returns:
            True если робот готов, False при истечении таймаута.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if self.robot.get_number_register(NR.scan_status) == NR_VAL.scan_good:
                    return True
            except Exception as e:
                self.logger.warning(f"Ошибка чтения R[2]: {e}")
            time.sleep(0.1)
        self.logger.warning("Таймаут ожидания R[2] = 1")
        return False

    def _wait_iteration_complete_no_stop(self, timeout: float = 30.0) -> bool:
        """Ожидает завершения итерации (R[1] = 2) без проверки stop_event.

        Используется при завершении работы, когда нужно дождаться
        окончания текущей итерации робота.

        Args:
            timeout: Максимальное время ожидания в секундах.

        Returns:
            True если итерация завершена, False при истечении таймаута.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed:
                    return True
            except Exception as e:
                self.logger.warning(f"Ошибка чтения R[1]: {e}")
            time.sleep(0.1)
        self.logger.warning("Таймаут ожидания R[1] = 2")
        return False

    def _parse_barcodes(self, raw_barcode: str) -> List[str]:
        """Парсит строку с баркодами, разделёнными символом ';'.

        Сохраняет позиционную привязку: пустые позиции остаются как ``""``
        чтобы индексы баркодов совпадали с физическими позициями в группе.

        Args:
            raw_barcode: Строка вида ``"barcode1;NoRead;barcode3"``
                или ``"NoRead"``.

        Returns:
            Список строк: баркод или ``""`` для пустых/NoRead позиций.
            Пустой список если вся строка пуста или единственный ``"NoRead"``.
        """
        if not raw_barcode:
            return []

        # Разделяем по ';' и очищаем
        barcodes = [b.strip() for b in raw_barcode.split(';')]

        self.logger.info(
            f"[PARSE] split по ';' -> {len(barcodes)} элементов: {barcodes}"
        )

        # Убираем пустые элементы от лишних ';' в начале и конце строки
        while barcodes and not barcodes[0]:
            barcodes.pop(0)
        while barcodes and not barcodes[-1]:
            barcodes.pop()

        if not barcodes:
            return []

        self.logger.info(f"[PARSE] после trim пустых краёв -> {barcodes}")

        # Заменяем NoRead и пустые на "" (сохраняя позицию)
        result = [("" if (not b or b == "NoRead") else b) for b in barcodes]

        self.logger.info(f"[PARSE] после замены NoRead -> {result}")

        # Если все позиции пустые — возвращаем пустой список
        if not any(result):
            return []

        return result

    # ==================== ФАЗА СКАНИРОВАНИЯ ====================

    def _scan_position_group(
        self, pallet_id: int, row: int, col_start: int, col_end: int
    ) -> List[TubeInfo]:
        """Сканирует группу позиций в одной строке штатива.

        Сканер возвращает несколько баркодов в одной строке, разделённых ';'.
        Например: ``"2701200911;2708770050;2707602822"``.

        Протокол взаимодействия с роботом:
        1. Ждём R[1] = 0 (робот готов).
        2. Устанавливаем SR[3] = "PP NN" (паллет, первая позиция).
        3. Устанавливаем SR[1] = "SCANNING_ITERATION".
        4. Устанавливаем R[1] = 1 (запуск).
        5. Ждём R[2] = 1 (робот в позиции).
        6. Выполняем сканирование (получаем группу баркодов).
        7. Устанавливаем R[2] = 0 (сканирование завершено).
        8. Ждём R[1] = 2 (итерация завершена).
        9. Робот сам сбрасывает R[1] = 0.

        Args:
            pallet_id: ID паллета (1 или 2).
            row: Номер ряда (0-9).
            col_start: Начальная колонка (включительно).
            col_end: Конечная колонка (не включительно).

        Returns:
            Список TubeInfo для найденных пробирок. Пустые позиции пропускаются.
        """
        positions = [row * 5 + col for col in range(col_start, col_end)]
        group_size = len(positions)
        first_position = positions[0]

        self.logger.debug(
            f"Сканирование П{pallet_id} ряд {row} колонки {col_start}-{col_end-1} "
            f"(позиции {positions})"
        )

        # --- Ожидание готовности робота ---
        if not self._wait_robot_ready():
            self.logger.error(f"Робот не готов для сканирования П{pallet_id} ряд {row}")
            return []

        # --- Установка регистров ---
        scan_data = f"{pallet_id:02d} {first_position:02d}"
        self.robot.set_string_register(SR.scan_data, scan_data)
        self.logger.debug(f"SR[3: SCAN_DATA] = '{scan_data}'")

        self.robot.set_string_register(SR.iteration_type, SR_VAL.scanning)
        self.logger.debug(f"SR[1: ITERATION_TYPE] = '{SR_VAL.scanning}'")

        # --- Запуск итерации ---
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.started)
        self.logger.debug("R[1] = 1 (итерация запущена)")

        # --- Ожидание позиционирования и сканирование ---
        scan_ready = self._wait_scan_ready()

        if scan_ready:
            raw_barcode, recv_time = self.scanner.scan(timeout=SCANNER_CFG.timeout)
            self.logger.info(
                f"[SCAN RAW] П{pallet_id} ряд={row} col={col_start}-{col_end-1} "
                f"positions={positions} raw='{raw_barcode}' "
                f"repr={repr(raw_barcode)} len={len(raw_barcode)} "
                f"recv_time={recv_time:.3f}с"
            )

            # Сигнализируем роботу что сканирование завершено
            self.robot.set_number_register(NR.scan_status, NR_VAL.scan_reset)
            self.logger.debug("R[2] = 0 (сканирование завершено)")
        else:
            # Остановка запрошена, но робот уже запущен - нужно дождаться и корректно завершить
            self.logger.warning(f"Остановка во время позиционирования П{pallet_id} ряд {row}")
            raw_barcode = ""
            # Ждём пока робот доедет (без проверки stop_event)
            self._wait_scan_ready_no_stop()
            # Сбрасываем R[2] = 0
            self.robot.set_number_register(NR.scan_status, NR_VAL.scan_reset)

        # --- Завершение итерации ---
        self._wait_iteration_complete_no_stop()

        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)
        self.logger.debug("R[1] = 0 (итерация сброшена)")

        # --- Парсинг и маппинг баркодов на позиции ---
        barcodes = self._parse_barcodes(raw_barcode)

        self.logger.info(
            f"[MAPPING] barcodes({len(barcodes)}) -> positions({group_size}): "
            f"barcodes={barcodes} positions={positions}"
        )

        # Создаём TubeInfo для каждого баркода (пустые строки = пустая позиция)
        tubes: List[TubeInfo] = []

        for i, barcode in enumerate(barcodes):
            if i >= group_size:
                self.logger.warning(f"Получено больше баркодов ({len(barcodes)}) чем позиций ({group_size})")
                break

            position = positions[i]
            self.logger.info(f"[MAPPING] i={i} barcode='{barcode}' -> position={position}")

            if not barcode:
                # Пустая позиция (NoRead) — пропускаем, но позиция сохранена
                self.logger.debug(f"П{pallet_id}[{position}] - пусто (NoRead)")
                continue

            self.logger.info(f"✓ П{pallet_id}[{position}] -> {barcode}")

            tube = TubeInfo(
                barcode=barcode,
                source_rack=pallet_id,
                number=position,
                test_type=TestType.UNKNOWN
            )
            tubes.append(tube)

        # Логируем позиции за пределами ответа сканера
        if len(barcodes) < group_size:
            for i in range(len(barcodes), group_size):
                position = positions[i]
                self.logger.debug(f"П{pallet_id}[{position}] - пусто")

        return tubes

    def _scan_all_source_racks(self) -> int:
        """Фаза 1: сканирование всех пробирок из исходных штативов.

        Каждый ряд сканируется за 2 итерации:
        - Группа 1: колонки 0, 1, 2 (3 пробирки).
        - Группа 2: колонки 3, 4 (2 пробирки).

        Отсканированные пробирки сразу отправляются в barcode_queue
        для параллельной обработки LISRequestThread.

        Returns:
            Количество отсканированных пробирок (с найденными баркодами).
        """
        self.logger.info("\n" + "=" * 60)
        self.logger.info("ФАЗА 1: СКАНИРОВАНИЕ ИСХОДНЫХ ШТАТИВОВ")
        self.logger.info("=" * 60 + "\n")

        total_scanned = 0

        # Получаем все исходные штативы
        source_pallets = self.rack_manager.get_all_source_pallets()

        for pallet in source_pallets:
            if self.stop_event.is_set():
                break

            pallet_id = pallet.pallet_id
            self.logger.info(f"\n--- Сканирование паллета П{pallet_id} (задержка 1с) ---")
            time.sleep(1.0)

            # Занимаем паллет
            pallet.occupy()
            pallet_tubes_count = 0

            try:
                # Сканируем 10 рядов
                for row in range(10):
                    if self.stop_event.is_set():
                        break

                    # --- Группа 1: колонки 0, 1, 2 (3 пробирки) ---
                    tubes_group1 = self._scan_position_group(
                        pallet_id, row, col_start=0, col_end=3
                    )

                    # Сразу отправляем в очередь для ЛИС
                    for tube in tubes_group1:
                        self.context.add_scanned_tube(tube)
                        self.rack_manager.add_scanned_tube(pallet_id, tube)
                        self.context.barcode_queue.put(ScanResult(tube=tube))
                        pallet_tubes_count += 1

                    # Проверка паузы
                    if not self._handle_pause_check():
                        break

                    # --- Группа 2: колонки 3, 4 (2 пробирки) ---
                    tubes_group2 = self._scan_position_group(
                        pallet_id, row, col_start=3, col_end=5
                    )

                    # Сразу отправляем в очередь для ЛИС
                    for tube in tubes_group2:
                        self.context.add_scanned_tube(tube)
                        self.rack_manager.add_scanned_tube(pallet_id, tube)
                        self.context.barcode_queue.put(ScanResult(tube=tube))
                        pallet_tubes_count += 1

                    # Проверка паузы
                    if not self._handle_pause_check():
                        break

                    # Прогресс каждые 2 ряда
                    if (row + 1) % 2 == 0:
                        self.logger.info(
                            f"П{pallet_id}: ряд {row + 1}/10, "
                            f"найдено {pallet_tubes_count} пробирок"
                        )

            finally:
                pallet.release()

            total_scanned += pallet_tubes_count
            self.logger.info(
                f"✓ Паллет П{pallet_id}: отсканировано {pallet_tubes_count} пробирок"
            )

        # Сигнализируем о завершении сканирования
        self.context.scanning_complete.set()

        if total_scanned == 0:
            self.logger.warning("Не найдено ни одной пробирки")
        else:
            self.logger.info(f"\n✓ Всего отсканировано: {total_scanned} пробирок")

        self.logger.info(f"\n{'=' * 60}")
        self.logger.info("✓ СКАНИРОВАНИЕ ЗАВЕРШЕНО")
        self.logger.info(f"{'=' * 60}\n")

        return total_scanned

    # ==================== ФАЗА СОРТИРОВКИ ====================

    def _execute_sorting_iteration(self, tube: TubeInfo) -> bool:
        """Выполняет одну итерацию физической сортировки пробирки.

        Протокол взаимодействия с роботом:
        1. Ждём R[1] = 0 (робот готов).
        2. Устанавливаем SR[2] = "SS TT DD RR".
        3. Устанавливаем SR[1] = "SORTING_ITERATION".
        4. Устанавливаем R[1] = 1 (запуск).
        5. Ждём R[1] = 2 (итерация завершена).
        6. Робот сам сбрасывает R[1] = 0.

        Args:
            tube: Информация о пробирке (баркод, источник, тип теста).

        Returns:
            True если пробирка успешно размещена, False при ошибке.
        """
        # --- Поиск целевого штатива ---
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

        # --- Ожидание готовности робота ---
        if not self._wait_robot_ready():
            self.logger.error("Робот не готов для сортировки")
            return False

        # --- Установка регистров ---
        movement_data = (
            f"{tube.source_rack:02d} "
            f"{tube.number:02d} "
            f"{dest_rack_id:02d} "
            f"{dest_position:02d}"
        )
        self.robot.set_string_register(SR.movement_data, movement_data)
        self.logger.debug(f"SR[2: MOVEMENT_DATA] = '{movement_data}'")

        self.robot.set_string_register(SR.iteration_type, SR_VAL.sorting)
        self.logger.debug(f"SR[1: ITERATION_TYPE] = '{SR_VAL.sorting}'")

        # --- Запуск итерации ---
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.started)
        self.logger.debug("R[1] = 1 (итерация запущена)")

        # --- Ожидание завершения ---
        iteration_ok = self._wait_iteration_complete()

        if not iteration_ok:
            # Остановка запрошена, но робот уже запущен - дождёмся завершения
            self.logger.warning("Остановка во время сортировки, ожидаем завершения итерации...")
            self._wait_iteration_complete_no_stop()

        # --- Сброс и обновление состояния ---
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)
        self.logger.debug("R[1] = 0 (итерация сброшена)")

        # Обновляем состояние
        dest_rack.add_tube(tube)
        self.rack_manager.mark_tube_sorted(tube.source_rack, tube.barcode)

        self.logger.info(
            f"✓ Пробирка размещена: Штатив #{dest_rack_id}[{tube.destination_number}] "
            f"({dest_rack.get_tube_count()}/{dest_rack.MAX_TUBES})"
        )

        return True

    def _sort_tubes_from_queue(self, total_tubes: int) -> None:
        """Фаза 2: сортировка пробирок из очереди ready_to_sort_queue.

        Пробирки сортируются в порядке получения ответов от ЛИС (FIFO).
        Пропускает пробирки с ошибочным или неизвестным типом теста.
        При отсутствии доступных штативов переходит в режим ожидания
        замены.

        Args:
            total_tubes: Общее количество пробирок для сортировки
                (используется для отображения прогресса).
        """
        self.logger.info("\n" + "=" * 60)
        self.logger.info("ФАЗА 2: ФИЗИЧЕСКАЯ СОРТИРОВКА ПРОБИРОК")
        self.logger.info("=" * 60 + "\n")

        processed = 0
        failed = 0
        skipped = 0

        while not self.stop_event.is_set():
            # --- Получение пробирки из очереди ---
            try:
                tube: TubeInfo = self.context.ready_to_sort_queue.get(timeout=0.5)
            except Empty:
                # Проверяем, всё ли обработано
                if self.context.lis_complete.is_set() and self.context.ready_to_sort_queue.empty():
                    break  # Всё отсортировано
                continue

            # --- Пропуск ошибочных пробирок ---
            if tube.test_type in [TestType.ERROR, TestType.UNKNOWN]:
                self.logger.warning(f"Пропуск {tube.barcode} (тип: {tube.test_type.name})")
                skipped += 1
                continue

            # --- Проверка доступности штатива ---
            dest_rack = self.rack_manager.find_available_rack(tube.test_type)

            if not dest_rack:
                self.logger.warning(f"Нет штативов для {tube.test_type.name}")
                self._enter_waiting_mode(f"Заполнены штативы типа {tube.test_type.name}")

                if not self._wait_for_rack_replacement():
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

            # --- Выполнение сортировки ---
            if self._execute_sorting_iteration(tube):
                processed += 1
                total_done = processed + skipped + failed
                if processed % 10 == 0 or total_done == total_tubes:
                    self.logger.info(
                        f"Прогресс: {total_done}/{total_tubes} "
                        f"(отсортировано: {processed}, пропущено: {skipped}, ошибок: {failed})"
                    )
            else:
                failed += 1
                self.logger.warning(f"✗ Ошибка сортировки {tube.barcode}")

            # Проверка паузы
            if not self._handle_pause_check():
                break

        self.logger.info(f"\n{'=' * 60}")
        self.logger.info("СОРТИРОВКА ЗАВЕРШЕНА")
        self.logger.info(f"Успешно: {processed}, Пропущено: {skipped}, Ошибок: {failed}")
        self.logger.info(f"{'=' * 60}\n")

    # ==================== УПРАВЛЕНИЕ ИЗ GUI ====================

    def request_pause(self):
        """Запрашивает паузу робота (вызывается из GUI-потока)."""
        self._pause_requested.set()
        self.logger.info("Пауза запрошена")

    def request_resume(self):
        """Запрашивает продолжение работы после паузы (вызывается из GUI-потока)."""
        # Отменяем запрос паузы если был
        self._pause_requested.clear()
        # Сигнал продолжения
        self._resume_event.set()
        self.logger.info("Продолжение запрошено")

    def confirm_rack_replaced(self):
        """Подтверждает замену штатива (вызывается из GUI-потока)."""
        self._rack_replaced_event.set()
        self.logger.info("Замена штатива подтверждена")

    def request_stop(self):
        """Запрашивает остановку с уходом робота в home (вызывается из GUI-потока)."""
        self._stop_requested.set()
        self.logger.info("Остановка запрошена (робот уйдёт в home)")

    def is_in_waiting_mode(self) -> bool:
        """Проверяет, находится ли робот в режиме ожидания.

        Returns:
            True если робот в home и ожидает команды оператора.
        """
        return self._in_waiting_mode

    # ==================== РЕЖИМ ОЖИДАНИЯ ====================

    def _handle_pause_check(self) -> bool:
        """Проверяет запрос на паузу и обрабатывает его.

        Если запрошена пауза:
        1. Отправляет робота в home через протокол PAUSE.
        2. Ждёт команды на продолжение от GUI.
        3. Выводит робота из режима ожидания.

        Returns:
            True если можно продолжать работу, False если установлен stop_event.
        """
        if not self._pause_requested.is_set():
            return not self.stop_event.is_set()

        # Сбрасываем запрос паузы
        self._pause_requested.clear()
        self._resume_event.clear()

        # Отправляем робота в home
        self._enter_waiting_mode("Пауза по команде оператора")

        # Ждём сигнала продолжения
        self.logger.info("Ожидание команды продолжения...")
        while not self.stop_event.is_set():
            if self._resume_event.wait(timeout=0.5):
                self._resume_event.clear()
                self._exit_waiting_mode()
                return True

        return False

    def _enter_waiting_mode(self, reason: str):
        """Переводит робота в режим ожидания (home позиция).

        Протокол:
        1. Python: R[4] = 0, SR[1] = "PAUSE", R[1] = 1.
        2. Робот: едет в home, ждёт R[4] = 1.

        Args:
            reason: Причина перехода в режим ожидания (для логирования).
        """
        self._in_waiting_mode = True

        self.logger.warning(f"\n{'=' * 60}")
        self.logger.warning(f"⏸ РЕЖИМ ОЖИДАНИЯ")
        self.logger.warning(f"Причина: {reason}")
        self.logger.warning(f"{'=' * 60}\n")

        # --- Ожидание готовности ---
        if not self._wait_robot_ready():
            self.logger.warning("Робот не готов для паузы")
            return

        # --- Установка регистров паузы ---
        self.robot.set_number_register(NR.pause_status, NR_VAL.pause_not_ready)

        self.robot.set_string_register(SR.iteration_type, SR_VAL.pause)
        self.logger.debug(f"SR[1] = '{SR_VAL.pause}'")

        # --- Запуск: робот поедет в home и будет ждать R[4] = 1 ---
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.started)
        self.logger.debug("R[1] = 1")

        self.logger.info("✓ Робот переходит в режим ожидания")

    def _exit_waiting_mode(self):
        """Выводит робота из режима ожидания.

        Протокол:
        1. Робот ждёт R[4] = 1.
        2. Python: R[4] = 1 (сигнал продолжения).
        3. Робот: выходит из WAIT, ставит R[1] = 2.
        4. Python: ждёт R[1] = 2, затем сбрасывает R[1] = 0.
        """
        self.logger.info("Выход из режима ожидания...")

        # Сигнализируем роботу выйти из WAIT
        self.robot.set_number_register(NR.pause_status, NR_VAL.pause_ready)

        # Ждём завершения итерации (робот установит R[1] = 2)
        if not self._wait_iteration_complete():
            self.logger.warning("Остановка: прервано ожидание выхода из паузы")
            self._in_waiting_mode = False
            return

        # Сбрасываем R[1] = 0 (Python должен сбросить)
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)

        self._in_waiting_mode = False
        self.logger.info("✓ Выход из режима ожидания завершён")

    def _wait_for_rack_replacement(self) -> bool:
        """Ожидает подтверждения замены штатива от оператора.

        Returns:
            True если получено подтверждение, False если установлен stop_event.
        """
        self._rack_replaced_event.clear()

        self.logger.info("Ожидание замены штатива...")

        while not self.stop_event.is_set():
            if self._rack_replaced_event.wait(timeout=0.5):
                self._rack_replaced_event.clear()
                self.logger.info("✓ Замена штатива подтверждена")
                return True

        return False

    def _go_to_home(self):
        """Отправляет робота в home позицию для завершения работы.

        Выполняет полный цикл: ожидание завершения текущей итерации,
        отправка команды PAUSE, ожидание прибытия в home, вывод из WAIT
        и сброс регистров.
        """
        self.logger.info("Отправка робота в home...")

        # --- Ожидание завершения текущей итерации ---
        timeout = 30.0
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                r1 = self.robot.get_number_register(NR.iteration_starter)
                if r1 == NR_VAL.ready:
                    break
                elif r1 == NR_VAL.completed:
                    # Итерация завершена (R[1] = 2), сбрасываем в 0
                    self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)
                    self.logger.debug("Сброшен R[1] = 0 после завершения итерации")
                    break
                # R[1] = 1 - итерация выполняется, ждём
            except Exception as e:
                self.logger.warning(f"Ошибка чтения регистра: {e}")
            time.sleep(0.1)
        else:
            self.logger.warning("Таймаут ожидания завершения текущей итерации")
            return

        # --- Отправка команды PAUSE ---
        self.robot.set_number_register(NR.pause_status, NR_VAL.pause_not_ready)
        self.robot.set_string_register(SR.iteration_type, SR_VAL.pause)
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.started)
        self.logger.info("Робот едет в home...")

        # Даём время на движение до home
        time.sleep(3.0)

        # --- Вывод робота из WAIT ---
        self.robot.set_number_register(NR.pause_status, NR_VAL.pause_ready)

        # Ждём завершения итерации (R[1] = 2)
        timeout = 10.0
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed:
                    break
            except Exception as e:
                self.logger.warning(f"Ошибка чтения регистра: {e}")
            time.sleep(0.1)

        # --- Сброс регистров ---
        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)

        self.logger.info("✓ Робот в home")

    def _check_stop_requested(self) -> bool:
        """Проверяет, запрошена ли остановка из GUI.

        Returns:
            True если остановка запрошена.
        """
        return self._stop_requested.is_set()

    # ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

    def _check_can_start_cycle(self) -> tuple[bool, str]:
        """Проверяет возможность начала нового цикла сканирования/сортировки.

        Проверяет наличие доступных штативов для каждого типа теста.

        Returns:
            Кортеж (can_start, reason): True и пустая строка если можно
            начинать, False и описание причины если нельзя.
        """
        required_types = [TestType.UGI, TestType.VPCH, TestType.UGI_VPCH, TestType.OTHER]

        for test_type in required_types:
            if not self.rack_manager.has_available_rack(test_type):
                return False, f"Нет штативов для типа {test_type.name}"

        return True, ""

    def _get_system_status(self) -> str:
        """Формирует текстовый отчёт о текущем состоянии системы.

        Включает информацию об исходных паллетах, целевых штативах
        и состоянии pipeline (счётчики ЛИС, размер очередей).

        Returns:
            Многострочная строка с отчётом для оператора.
        """
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

        # Статус pipeline
        with self.context.counter_lock:
            sent = self.context.sent_to_lis
            received = self.context.received_from_lis

        lines.append(f"\nPIPELINE:")
        lines.append(f"  Отправлено в ЛИС: {sent}")
        lines.append(f"  Получено от ЛИС: {received}")
        lines.append(f"  В очереди на сортировку: {self.context.ready_to_sort_queue.qsize()}")

        return "\n".join(lines)

    def _print_statistics(self):
        """Выводит в лог статистику по типам тестов отсканированных пробирок."""
        all_tubes = self.context.get_all_scanned_tubes()

        if not all_tubes:
            return

        stats = {}
        for tube in all_tubes:
            stats[tube.test_type] = stats.get(tube.test_type, 0) + 1

        self.logger.info("\n📊 Статистика по типам тестов:")
        for test_type, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            self.logger.info(f"  {test_type.name}: {count} шт")

    # ==================== ГЛАВНЫЙ ЦИКЛ ====================

    def run(self) -> None:
        """Главный цикл работы робота.

        Выполняет бесконечный цикл до stop_event:
        1. Проверка готовности штативов.
        2. Фаза 1: сканирование всех паллетов.
        3. Фаза 2: сортировка пробирок из очереди.
        4. Ожидание замены исходных штативов для следующего цикла.

        При завершении корректно выводит робота из режима ожидания
        или отправляет в home.
        """
        self.logger.info("[Robot] Поток запущен")

        try:
            # --- Подготовка робота ---
            self.logger.info("Подготовка робота...")
            self.robot.stop_all_running_programms()
            time.sleep(0.5)
            self.robot.reset_errors()
            time.sleep(0.5)
            self.robot.start_program(ROBOT_CFG.robot_program_name)
            time.sleep(1.0)
            self.logger.info("✓ Робот готов!")

            # --- Основной цикл ---
            while not self.stop_event.is_set():
                # Сброс контекста для нового цикла
                self.context.reset()

                # 1. Проверка готовности
                can_start, reason = self._check_can_start_cycle()

                if not can_start:
                    self._enter_waiting_mode(reason)
                    if not self._wait_for_rack_replacement():
                        continue
                    self._exit_waiting_mode()

                # 2. ФАЗА 1: Сканирование (в этом потоке)
                # Пробирки сразу отправляются в barcode_queue
                total_scanned = self._scan_all_source_racks()

                if self.stop_event.is_set():
                    break

                if total_scanned == 0:
                    self._enter_waiting_mode("Нет пробирок в штативах")
                    if not self._wait_for_rack_replacement():
                        continue
                    self._exit_waiting_mode()
                    self.rack_manager.reset_all_source_pallets()
                    continue

                # 3. ФАЗА 2: Сортировка (пробирки берём из ready_to_sort_queue)
                self._sort_tubes_from_queue(total_scanned)

                # Статистика
                self._print_statistics()

                if self.stop_event.is_set():
                    break

                # 4. Завершение цикла
                self.logger.info("\n" + "=" * 60)
                self.logger.info("✓ ЦИКЛ ЗАВЕРШЁН")
                self.logger.info("=" * 60 + "\n")

                self.rack_manager.clear_sorted_tubes()

                self._enter_waiting_mode("Требуется замена исходных штативов")

                if not self._wait_for_rack_replacement():
                    continue

                self._exit_waiting_mode()
                self.rack_manager.reset_all_source_pallets()

        except Exception as e:
            self.logger.fatal(f"Критическая ошибка: {e}", exc_info=True)

        finally:
            # --- Корректное завершение ---
            try:
                if self._in_waiting_mode:
                    # Робот уже в home, но застрял в WAIT - выводим его
                    self.logger.info("Робот в режиме ожидания (уже в home), завершаем итерацию...")
                    try:
                        # Сигнализируем роботу выйти из WAIT (R[4] = 1)
                        self.robot.set_number_register(NR.pause_status, NR_VAL.pause_ready)

                        # Ждём завершения итерации (R[1] = 2) с таймаутом
                        timeout = 10.0
                        start_time = time.time()
                        while time.time() - start_time < timeout:
                            try:
                                if self.robot.get_number_register(NR.iteration_starter) == NR_VAL.completed:
                                    break
                            except Exception:
                                pass
                            time.sleep(0.1)

                        # Сбрасываем R[1] = 0
                        self.robot.set_number_register(NR.iteration_starter, NR_VAL.ready)
                        self.logger.info("✓ Робот в home, итерация завершена")

                    except Exception as e:
                        self.logger.warning(f"Ошибка при завершении итерации: {e}")

                elif self._stop_requested.is_set():
                    # Робот не в режиме ожидания - отправляем в home
                    self._go_to_home()

            except Exception as e:
                self.logger.warning(f"Ошибка при завершении: {e}")

            self.logger.info("[Robot] Поток завершён")
