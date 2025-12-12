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
    RackStatus,
    RackOccupancy,
    TubeInfo,
    SourceRack,
    DestinationRack,
    RackSystemManager,
)

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
    logger = create_logger("ProjectR.Robot", "robot.log")
    
    return {
        "loader": logger,
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
    
    # logger.info(f"Инициализировано {len(source_racks)} исходных штативов")
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
            target_count=50
        ),
        
        # Rack 1-2: UGI (pcr-1)
        DestinationRack(
            rack_id=1,
            test_type=TestType.UGI,
            target_count=50
        ),
        DestinationRack(
            rack_id=2,
            test_type=TestType.UGI,
            target_count=50
        ),
        
        # Rack 3-4: VPCH (pcr-2)
        DestinationRack(
            rack_id=3,
            test_type=TestType.VPCH,
            target_count=50
        ),
        DestinationRack(
            rack_id=4,
            test_type=TestType.VPCH,
            target_count=50
        ),
        
        # Rack 5-6: UGI_VPCH (оба теста)
        DestinationRack(
            rack_id=5,
            test_type=TestType.UGI_VPCH,
            target_count=50
        ),
        DestinationRack(
            rack_id=6,
            test_type=TestType.UGI_VPCH,
            target_count=50
        ),
    ]
    
    # logger.info(f"Инициализировано {len(destination_racks)} целевых штативов")
    return destination_racks


def initialize_rack_system_manager() -> RackSystemManager:
    """
    Инициализирует RackSystemManager с исходными и целевыми штативами.
    
    Returns:
        Настроенный RackSystemManager
    """
    # Создаём исходные и целевые штативы
    source_racks = initialize_source_racks()
    destination_racks = initialize_destination_racks()
    
    # Создаём менеджер
    rack_manager = RackSystemManager(
        source_pallets=source_racks,
        destination_racks=destination_racks
    )
    
    # logger.info("RackSystemManager инициализирован")
    # logger.info(f"  - Исходных штативов: {len(source_racks)}")
    # logger.info(f"  - Целевых штативов: {len(destination_racks)}")
    
    return rack_manager


def initialize_devices(config) -> tuple:
    """
    Инициализирует и подключает робота и сканер.
    
    Args:
        config: Конфигурация робота
    
    Returns:
        (robot, scanner) - подключённые устройства
    
    Raises:
        Exception: При ошибке подключения к устройствам
    """
    # logger.info("Подключение к устройствам...")
    
    # Создаём робота
    robot = RobotAgilebot(
        host=config.ip,
        name=config.name
    )
    
    # Создаём сканер
    scanner = ScannerHikrobotTCP(
        host=config.scanner.ip,
        port=config.scanner.port,
        timeout=config.scanner.timeout
    )
    
    # Подключаемся
    try:
        robot.connect()
        # logger.info("✓ Робот подключен")
        
        scanner.connect()
        # logger.info("✓ Сканер подключен")
        
        return robot, scanner
        
    except Exception as e:
        # logger.error(f"Ошибка подключения к устройствам: {e}")
        # Пытаемся отключить уже подключённые устройства
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
    # Создаём событие остановки
    stop_event = threading.Event()
    
    # Настраиваем логирование
    loggers = build_loggers()
    install_global_exception_hooks()
    # logger.info("Запуск системы сортировки пробирок...")
    
    # Загружаем конфигурацию
    config = ROBOT_CFG
    # logger.info(f"Конфигурация загружена:")
    # logger.info(f"  - Робот: {config.ip}")
    # logger.info(f"  - Сканер: {config.scanner.ip}:{config.scanner.port}")
    # logger.info(f"  - ЛИС: {config.lis.ip}:{config.lis.port}")
    
    # Инициализируем устройства
    try:
        robot, scanner = initialize_devices(config)
    except Exception as e:
        # logger.error(f"Не удалось инициализировать устройства: {e}")
        # logger.error("Система не может быть запущена")
        return
    
    # Инициализируем RackSystemManager
    try:
        rack_manager = initialize_rack_system_manager()
    except Exception as e:
        # logger.error(f"Ошибка инициализации RackSystemManager: {e}")
        # Отключаем устройства
        try:
            robot.disconnect()
            scanner.disconnect()
        except:
            pass
        return
    
    # Создаём главный поток обработки
    # logger.info("Создание главного потока обработки...")
    main_thread = RobotThread(
        robot=robot,
        scanner=scanner,
        rack_manager=rack_manager,
        lis_host=config.lis.ip,
        lis_port=config.lis.port,
        logger=loggers["loader"],
        stop_event=stop_event
    )
    
    # Запускаем главный поток
    # logger.info("Запуск главного потока обработки...")
    main_thread.start()
    # logger.info("✓ Система запущена и готова к работе")
    
    # Главный цикл - ожидание работы потока
    try:
        while main_thread.is_alive():
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        # logger.info("Получен сигнал остановки (Ctrl+C)")
        print("\n⚠ Остановка системы...")
    
    # Останавливаем потоки
    # logger.info("Остановка рабочих потоков...")
    stop_event.set()
    
    # Ждём завершения главного потока
    main_thread.join(timeout=10.0)
    if main_thread.is_alive():
        pass
        # logger.warning("Главный поток не завершился за отведённое время")
    else:
        pass
        # logger.info("✓ Главный поток остановлен")
    
    # Отключаем устройства
    # logger.info("Отключение устройств...")
    
    try:
        robot.disconnect()
        # logger.info("✓ Робот отключен")
    except Exception as e:
        print(e)
        # logger.error(f"Ошибка отключения робота: {e}")
    
    try:
        scanner.disconnect()
        # logger.info("✓ Сканер отключен")
    except Exception as e:
        print(e)
        # logger.error(f"Ошибка отключения сканера: {e}")
    
    # Финальный статус системы
    
    try:
        status = rack_manager.get_system_status()
        # logger.info(status)
    except Exception as e:
        print(e)
        # logger.error(f"Ошибка получения статуса: {e}")
    
    print("\n✓ Система остановлена.")


if __name__ == "__main__":
    """Точка входа при прямом запуске модуля"""
    run_workcell()