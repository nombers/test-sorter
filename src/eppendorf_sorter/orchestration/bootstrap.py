# src/eppendorf_sorter/orchestration/bootstrap.py
"""
Модуль инициализации и запуска системы сортировки пробирок.
Настраивает устройства, менеджеры штативов и запускает главный поток обработки.
"""
import threading
import logging
import time
from typing import Dict, List

from src.eppendorf_sorter.logging import (
    create_logger, 
    install_global_exception_hooks,
)

from src.eppendorf_sorter.config import (
    load_system_layout_config,
    load_robot_config,
)

from src.eppendorf_sorter.devices import (
    CellRobot,
    Scanner,
    RobotAgilebot,
    ScannerHikrobotTCP,
)

from src.eppendorf_sorter.domain.racks import (
    TestType,
    SourceRack,
    DestinationRack,
    RackSystemManager,
)

from src.eppendorf_sorter.lis import LISClient
from src.eppendorf_sorter.orchestration.robot_logic import RobotThread
from src.eppendorf_sorter.orchestration.shutdown import shutdown


# Загрузка конфигурации
ROBOT_CFG = load_robot_config()
ROBOT_SCANNER = ROBOT_CFG.scanner
ROBOT_LIS = ROBOT_CFG.lis


def build_loggers() -> Dict[str, logging.Logger]:
    """
    Создаёт логгеры для различных компонентов системы.
    
    Returns:
        Словарь с логгерами
    """
    logger_robot = create_logger("ProjectR.Robot", "robot.log")
    
    return {
        "robot": logger_robot,
    }


def initialize_source_racks() -> List[SourceRack]:
    """
    Инициализирует исходные штативы (паллеты) для сканирования.
    
    Returns:
        Список настроенных SourceRack
    """
    source_racks = [
        SourceRack(pallet_id=0),  # П0
        SourceRack(pallet_id=1),  # П1
    ]
    
    return source_racks


def initialize_destination_racks() -> List[DestinationRack]:
    """
    Инициализирует целевые штативы для размещения пробирок.
    
    Структура:
    - Rack 0: OTHER (разное)
    - Rack 1-2: UGI (pcr-1)
    - Rack 3-4: VPCH (pcr-2)
    - Rack 5-6: UGI_VPCH (оба теста)
    
    Returns:
        Список настроенных DestinationRack
    """
    destination_racks = [
        # Rack 0: Разное (OTHER)
        DestinationRack(
            rack_id=0,
            test_type=TestType.OTHER,
            target=50
        ),
        
        # Rack 1-2: UGI (pcr-1)
        DestinationRack(
            rack_id=1,
            test_type=TestType.UGI,
            target=50
        ),
        DestinationRack(
            rack_id=2,
            test_type=TestType.UGI,
            target=50
        ),
        
        # Rack 3-4: VPCH (pcr-2)
        DestinationRack(
            rack_id=3,
            test_type=TestType.VPCH,
            target=50
        ),
        DestinationRack(
            rack_id=4,
            test_type=TestType.VPCH,
            target=50
        ),
        
        # Rack 5-6: UGI_VPCH (оба теста)
        DestinationRack(
            rack_id=5,
            test_type=TestType.UGI_VPCH,
            target=50
        ),
        DestinationRack(
            rack_id=6,
            test_type=TestType.UGI_VPCH,
            target=50
        ),
    ]
    
    return destination_racks


def initialize_rack_system_manager(logger: logging.Logger) -> RackSystemManager:
    """
    Инициализирует RackSystemManager с исходными и целевыми штативами.
    
    Args:
        logger: Логгер для вывода информации
    
    Returns:
        Настроенный RackSystemManager
    """
    source_racks = initialize_source_racks()
    destination_racks = initialize_destination_racks()
    
    rack_manager = RackSystemManager()
    
    # Инициализируем систему
    rack_manager.initialize_system(
        pallets_list=source_racks,
        racks_list=destination_racks
    )
    
    logger.info("RackSystemManager инициализирован")
    logger.info(f"  - Исходных паллетов: {len(source_racks)}")
    logger.info(f"  - Целевых штативов: {len(destination_racks)}")
    
    return rack_manager


def initialize_devices(config, logger: logging.Logger) -> tuple[CellRobot, Scanner]:
    """
    Инициализирует и подключает робота и сканер.
    
    Args:
        config: Конфигурация робота
        logger: Логгер для вывода информации
    
    Returns:
        (robot, scanner) - подключённые устройства
    
    Raises:
        Exception: При ошибке подключения к устройствам
    """
    logger.info("Подключение к устройствам...")
    
    robot = RobotAgilebot(
        name=config.name,
        ip=config.ip
    )
    
    scanner = ScannerHikrobotTCP(
        name=config.scanner.name,
        ip=config.scanner.ip,
        port=config.scanner.port
    )
    
    try:
        robot.connect()
        logger.info("✓ Робот подключен")
        
        scanner.connect()
        logger.info("✓ Сканер подключен")
        
        return robot, scanner
        
    except Exception as e:
        logger.error(f"Ошибка подключения к устройствам: {e}")
        try:
            robot.disconnect()
        except:
            pass
        try:
            scanner.disconnect()
        except:
            pass
        raise


def run_workcell():
    """
    Главная функция запуска системы сортировки.
    
    Инициализирует все компоненты, запускает главный поток обработки
    и обрабатывает корректное завершение работы.
    """
    # 0. Создаем объект для управления потоками
    stop_event = threading.Event()
    
    # 1. Логгеры + хуки
    loggers = build_loggers()
    install_global_exception_hooks()
    
    loggers["robot"].info("="*60)
    loggers["robot"].info("ЗАПУСК СИСТЕМЫ СОРТИРОВКИ ПРОБИРОК")
    loggers["robot"].info("="*60)
    
    # 2. Загружаем конфигурацию
    config = ROBOT_CFG
    loggers["robot"].info("\nКонфигурация загружена:")
    loggers["robot"].info(f"  - Робот: {config.name} ({config.ip})")
    loggers["robot"].info(f"  - Сканер: {config.scanner.name} ({config.scanner.ip}:{config.scanner.port})")
    loggers["robot"].info(f"  - ЛИС: {config.lis.ip}:{config.lis.port}")
    
    # 3. Инициализируем устройства
    try:
        robot, scanner = initialize_devices(config, loggers["robot"])
    except Exception as e:
        loggers["robot"].error(f"\n❌ Не удалось инициализировать устройства: {e}")
        loggers["robot"].error("Система не может быть запущена")
        return
    
    # 4. Инициализируем RackSystemManager
    try:
        rack_manager = initialize_rack_system_manager(loggers["robot"])
    except Exception as e:
        loggers["robot"].error(f"\n❌ Ошибка инициализации RackSystemManager: {e}")
        try:
            robot.disconnect()
            scanner.disconnect()
        except:
            pass
        return
    
    # 5. Инициализируем LIS клиент
    lis_client = LISClient(
        host=config.lis.ip,
        port=config.lis.port,
        max_workers=20
    )
    loggers["robot"].info("✓ LIS клиент инициализирован (workers=20)")
    
    # 6. Создаём главный поток обработки
    loggers["robot"].info("\nСоздание главного потока обработки...")
    robot_thread = RobotThread(
        rack_manager=rack_manager,
        robot=robot,
        scanner=scanner,
        lis_client=lis_client,
        logger=loggers["robot"],
        stop_event=stop_event
    )
    
    # 7. Собираем все потоки, которыми управляем
    threads: list[threading.Thread] = [
        robot_thread,
    ]
    
    # 8. Запускаем главный поток
    loggers["robot"].info("Запуск главного потока обработки...")
    robot_thread.start()
    
    loggers["robot"].info("\n" + "="*60)
    loggers["robot"].info("✓ СИСТЕМА ЗАПУЩЕНА И ГОТОВА К РАБОТЕ")
    loggers["robot"].info("="*60 + "\n")
    
    # 9. Основной цикл / ожидание
    try:
        loggers["robot"].info("Рабочая ячейка запущена. Нажмите Ctrl+C для остановки.")
        # Ждём, пока нас не убьют Ctrl+C
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        loggers["robot"].info("\n" + "="*60)
        loggers["robot"].info("⚠ Получен сигнал остановки (Ctrl+C)")
        loggers["robot"].info("="*60)
        print("\n⚠ Остановка системы...")
    
    finally:
        # 10. Аккуратный shutdown
        loggers["robot"].info("\nНачало процедуры остановки...")
        shutdown(stop_event=stop_event, threads=threads, logger=loggers["robot"])
        
        # Останавливаем LIS клиент
        loggers["robot"].info("Остановка LIS клиента...")
        try:
            lis_client.shutdown()
            loggers["robot"].info("✓ LIS клиент остановлен")
        except Exception as e:
            loggers["robot"].error(f"❌ Ошибка остановки LIS клиента: {e}")
        
        # Гасим робота
        loggers["robot"].info("Отключение робота...")
        try:
            robot.stop_all_running_programms()
            robot.disconnect()
            loggers["robot"].info("✓ Робот отключен")
        except Exception as e:
            loggers["robot"].error(f"❌ Ошибка при отключении робота: {e}")
        
        # Гасим сканер
        loggers["robot"].info("Отключение сканера...")
        try:
            scanner.disconnect()
            loggers["robot"].info("✓ Сканер отключен")
        except Exception as e:
            loggers["robot"].error(f"❌ Ошибка отключения сканера: {e}")
        
        loggers["robot"].info("\n" + "="*60)
        loggers["robot"].info("✓ СИСТЕМА ПОЛНОСТЬЮ ОСТАНОВЛЕНА")
        loggers["robot"].info("="*60)
        print("\n✓ Система остановлена.")