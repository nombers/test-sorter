"""
Главная логика сортировки пробирок.
Работает на потоках вместо асинхронности.
"""
import logging
import threading
import time
from typing import List, Optional, Tuple

from ..devices import CellRobot, Scanner
from ..domain.racks import RackManager, TubeInfo, TestType
from ..lis import LISClient
from .robot_protocol import NR, NR_VAL, SR, SR_VAL

logger = logging.getLogger("Sorter.Main")

# Константы для сканирования
X_STEP = 20.7
Y_STEP = 20.7


def _wait_until(condition, stop_event: threading.Event, poll: float = 0.1, timeout: float = 30.0) -> bool:
    """
    Ожидает выполнения условия с проверкой stop_event.
    
    Args:
        condition: callable, возвращающий bool
        stop_event: событие остановки
        poll: интервал проверки (сек)
        timeout: максимальное время ожидания (сек)
        
    Returns:
        True если условие выполнено, False если timeout или stop
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if stop_event.is_set():
            logger.info("Получена команда остановки во время ожидания")
            return False
        if condition():
            return True
        time.sleep(poll)
    logger.warning(f"Таймаут ожидания условия ({timeout} сек)")
    return False


def scan_three_positions(
    scanner: Scanner,
    x: float,
    y: float,
    z: float,
    row: int,
    cols_range: range,
    matrix: List[List[Optional[str]]]
) -> List[str]:
    """
    Сканирует 3 позиции в одном ряду.
    Упрощённая версия: если NoRead - считаем что пробирки нет.
    
    Args:
        scanner: сканер
        x, y, z: начальная позиция
        row: номер ряда
        cols_range: range колонок для сканирования
        matrix: матрица для сохранения результатов
        
    Returns:
        Список из 3 баркодов (может содержать "NoRead")
    """
    barcodes = []
    for col in cols_range:
        scan_x = x + col * X_STEP
        # В реальности здесь должно быть перемещение робота к позиции (scan_x, y, z)
        # Пока просто сканируем
        
        try:
            code, _ = scanner.scan(timeout=2.5)
            barcodes.append(code)
            matrix[row][col] = code if code != "NoRead" else None
        except Exception as e:
            logger.error(f"Ошибка сканирования [{row},{col}]: {e}")
            barcodes.append("NoRead")
            matrix[row][col] = None
    
    return barcodes


def scan_source_rack(
    scanner: Scanner,
    robot: CellRobot,
    source_id: int,
    matrix: List[List[Optional[str]]],
    scan_position: Tuple[float, float, float],
    lis_client: LISClient,
    stop_event: threading.Event
) -> List[TubeInfo]:
    """
    Сканирует исходный штатив построчно, затем делает пакетный запрос к ЛИС.
    
    Args:
        scanner: сканер
        robot: робот
        source_id: ID исходного штатива (0 или 1)
        matrix: матрица 10x5 для сохранения баркодов
        scan_position: координаты (x, y, z) начальной точки сканирования
        lis_client: клиент для запросов к ЛИС
        stop_event: событие остановки
        
    Returns:
        Список TubeInfo с типами тестов
    """
    logger.info(f"Сканирование штатива П{source_id}")
    x, y, z = scan_position
    
    all_barcodes_with_positions = []
    
    # Сканируем построчно
    for row in range(10):
        if stop_event.is_set():
            logger.info("Остановка сканирования по команде")
            return []
        
        # Первые 3 колонки
        barcodes_1 = scan_three_positions(scanner, x, y, z, row, range(0, 3), matrix)
        
        # Последние 2 колонки
        barcodes_2 = scan_three_positions(scanner, x, y, z, row, range(3, 5), matrix)
        
        # Собираем баркоды с позициями
        all_barcodes = barcodes_1 + barcodes_2
        for col, barcode in enumerate(all_barcodes):
            if barcode and barcode != "NoRead":
                all_barcodes_with_positions.append({
                    "barcode": barcode,
                    "source_rack_id": source_id,
                    "row": row,
                    "col": col
                })
        
        logger.debug(f"Строка {row}: {all_barcodes}")
    
    # Извлекаем только баркоды для запроса к ЛИС
    barcodes_only = [item["barcode"] for item in all_barcodes_with_positions]
    
    if not barcodes_only:
        logger.info(f"Штатив П{source_id} пуст")
        return []
    
    # Пакетный запрос к ЛИС
    logger.info(f"Запрос типов тестов для {len(barcodes_only)} баркодов из П{source_id}")
    barcode_to_type = lis_client.get_tube_types_batch(barcodes_only)
    
    # Формируем список TubeInfo
    tubes = []
    for item in all_barcodes_with_positions:
        barcode = item["barcode"]
        test_type = barcode_to_type.get(barcode, TestType.ERROR)
        
        tube_info = TubeInfo(
            barcode=barcode,
            test_type=test_type,
            source_rack_id=item["source_rack_id"],
            source_row=item["row"],
            source_col=item["col"]
        )
        tubes.append(tube_info)
    
    logger.info(f"Получено {len(tubes)} пробирок из П{source_id}")
    return tubes


def process_single_tube(
    robot: CellRobot,
    scanner: Scanner,
    rack_manager: RackManager,
    tube: TubeInfo,
    stop_event: threading.Event
) -> bool:
    """
    Обрабатывает одну пробирку: находит рэк, перемещает, сканирует.
    
    Args:
        robot: робот
        scanner: сканер
        rack_manager: менеджер штативов
        tube: информация о пробирке
        stop_event: событие остановки
        
    Returns:
        True если пробирка успешно размещена, False если нет
    """
    if stop_event.is_set():
        return False
    
    logger.info(f"Обработка пробирки {tube.barcode} (тип: {tube.test_type.name})")
    
    # Находим доступный рэк
    rack = rack_manager.find_available_rack(tube.test_type)
    if rack is None:
        logger.error(f"Нет доступных рэков для типа {tube.test_type.name}")
        return False
    
    # Вычисляем номер пробирки в исходном штативе (0-49)
    tripod_tube_num = tube.source_row * 5 + tube.source_col
    
    # Находим свободную позицию в целевом рэке
    rack_tube_num = rack.find_next_free_position()
    if rack_tube_num is None:
        logger.error(f"Рэк {rack.rack_id} переполнен")
        return False
    
    # Формируем данные для робота: "SS TT DD RR"
    # SS = source_tripod_id (П0=00, П1=01)
    # TT = tube_num в штативе (00-49)
    # DD = dest_rack_id (00-06)
    # RR = rack_tube_num в целевом рэке (00-49)
    data_str = f"{tube.source_rack_id:02d} {tripod_tube_num:02d} {rack.rack_id:02d} {rack_tube_num:02d}"
    
    logger.info(f"Отправка данных роботу: '{data_str}'")
    
    # Отправляем данные в String Register
    robot.set_string_register(SR.loading_data, data_str)
    
    # Устанавливаем тип итерации
    robot.set_string_register(SR.iteration_type, SR_VAL.loading)
    
    # Стартуем итерацию
    robot.set_number_register(NR.iteration_starter, NR_VAL.start)
    
    # Ждём статус захвата
    logger.debug("Ожидание статуса захвата...")
    success = _wait_until(
        lambda: robot.get_number_register(NR.grip_status) != NR_VAL.grip_reset,
        stop_event,
        timeout=30.0
    )
    
    if not success:
        logger.warning("Таймаут ожидания статуса захвата")
        return False
    
    grip_status = robot.get_number_register(NR.grip_status)
    if grip_status == NR_VAL.grip_bad:
        logger.warning(f"Пробирка {tube.barcode} не захвачена роботом")
        # Сбрасываем статусы
        robot.set_number_register(NR.grip_status, NR_VAL.grip_reset)
        robot.set_number_register(NR.iteration_starter, NR_VAL.reset)
        return False
    
    logger.info(f"Пробирка {tube.barcode} захвачена успешно")
    
    # Ждём команду на сканирование (робот установит move_status=0 когда готов)
    logger.debug("Ожидание готовности к сканированию...")
    success = _wait_until(
        lambda: robot.get_number_register(NR.move_status) == NR_VAL.move_stop,
        stop_event,
        timeout=30.0
    )
    
    if not success:
        logger.warning("Таймаут ожидания готовности к сканированию")
        return False
    
    # Сканируем QR код
    logger.debug("Сканирование QR кода...")
    try:
        qr_code, scan_delay = scanner.scan(timeout=2.5)
        
        if qr_code == "NoRead" or not qr_code:
            logger.warning(f"QR код не считан для {tube.barcode}")
            robot.set_number_register(NR.scan_status, NR_VAL.scan_bad)
            robot.set_number_register(NR.scan_delay, 0.0)
            # Сбрасываем итерацию
            robot.set_number_register(NR.iteration_starter, NR_VAL.reset)
            return False
        
        logger.info(f"QR код считан: {qr_code} (задержка: {scan_delay:.2f} сек)")
        
        # Отправляем статус и задержку роботу
        robot.set_number_register(NR.scan_status, NR_VAL.scan_good)
        robot.set_number_register(NR.scan_delay, scan_delay)
        
    except Exception as e:
        logger.error(f"Ошибка сканирования QR: {e}")
        robot.set_number_register(NR.scan_status, NR_VAL.scan_bad)
        robot.set_number_register(NR.scan_delay, 0.0)
        robot.set_number_register(NR.iteration_starter, NR_VAL.reset)
        return False
    
    # Добавляем пробирку в рэк
    rack.add_tube(tube)
    logger.info(f"Пробирка {tube.barcode} добавлена в рэк {rack.rack_id}, позиция {rack_tube_num}")
    
    # Ждём завершения итерации
    logger.debug("Ожидание завершения итерации...")
    success = _wait_until(
        lambda: robot.get_number_register(NR.iteration_starter) == NR_VAL.end,
        stop_event,
        timeout=30.0
    )
    
    if not success:
        logger.warning("Таймаут ожидания завершения итерации")
        return False
    
    # Сбрасываем регистры
    robot.set_number_register(NR.iteration_starter, NR_VAL.reset)
    robot.set_number_register(NR.grip_status, NR_VAL.grip_reset)
    robot.set_number_register(NR.scan_status, NR_VAL.scan_reset)
    
    logger.info(f"Пробирка {tube.barcode} успешно обработана")
    return True


class MainLogicThread(threading.Thread):
    """
    Главный поток обработки пробирок.
    """
    
    def __init__(
        self,
        robot: CellRobot,
        scanner: Scanner,
        rack_manager: RackManager,
        lis_host: str,
        lis_port: int,
        source_racks_config: List[dict],
        stop_event: threading.Event
    ):
        super().__init__(name="MainLogicThread", daemon=False)
        self.robot = robot
        self.scanner = scanner
        self.rack_manager = rack_manager
        self.lis_host = lis_host
        self.lis_port = lis_port
        self.source_racks_config = source_racks_config
        self.stop_event = stop_event
        self.lis_client: Optional[LISClient] = None
    
    def run(self):
        """Главный цикл обработки."""
        logger.info("Запуск главного потока обработки")
        
        try:
            # Инициализируем LIS клиент
            self.lis_client = LISClient(self.lis_host, self.lis_port, max_workers=10)
            
            # Подготавливаем робота
            logger.info("Подготовка робота...")
            self.robot.stop_all_running_programms()
            time.sleep(0.5)
            self.robot.reset_errors()
            time.sleep(0.5)
            self.robot.start_program("loader_program")
            time.sleep(1.0)
            
            logger.info("Робот готов к работе")
            
            # Главный цикл сортировки
            while not self.stop_event.is_set():
                logger.info("=" * 60)
                logger.info("НАЧАЛО НОВОГО ЦИКЛА СОРТИРОВКИ")
                logger.info("=" * 60)
                
                # Сканируем все исходные штативы параллельно
                all_tubes = []
                for source_config in self.source_racks_config:
                    tubes = scan_source_rack(
                        self.scanner,
                        self.robot,
                        source_config["id"],
                        source_config["matrix"],
                        source_config["scan_position"],
                        self.lis_client,
                        self.stop_event
                    )
                    all_tubes.extend(tubes)
                
                if self.stop_event.is_set():
                    break
                
                logger.info(f"Всего пробирок для обработки: {len(all_tubes)}")
                
                # Обрабатываем каждую пробирку
                processed = 0
                failed = 0
                
                for tube in all_tubes:
                    if self.stop_event.is_set():
                        break
                    
                    success = process_single_tube(
                        self.robot,
                        self.scanner,
                        self.rack_manager,
                        tube,
                        self.stop_event
                    )
                    
                    if success:
                        processed += 1
                    else:
                        failed += 1
                    
                    # Проверяем заполненность рэков после каждой пробирки
                    full_racks = self.rack_manager.get_full_racks()
                    if full_racks:
                        logger.warning(f"Заполненные рэки: {[r.rack_id for r in full_racks]}")
                        # TODO: Реализовать обработку заполненных рэков
                
                logger.info("=" * 60)
                logger.info(f"ЦИКЛ ЗАВЕРШЁН: обработано {processed}, ошибок {failed}")
                logger.info("=" * 60)
                
                if self.stop_event.is_set():
                    break
                
                # Уведомляем о необходимости замены штативов
                logger.info(">>> ЗАМЕНИТЕ ИСХОДНЫЕ ШТАТИВЫ <<<")
                # TODO: Реализовать механизм ожидания подтверждения замены
                time.sleep(5.0)  # Временная заглушка
        
        except Exception as e:
            logger.error(f"Критическая ошибка в главном потоке: {e}", exc_info=True)
        
        finally:
            if self.lis_client:
                self.lis_client.shutdown()
            logger.info("Главный поток завершён")
