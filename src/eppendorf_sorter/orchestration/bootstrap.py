# src/eppendorf_sorter/orchestration/bootstrap.py
"""Модуль инициализации и запуска системы сортировки пробирок.

Настраивает устройства, менеджеры штативов и запускает потоки обработки.

АРХИТЕКТУРА ПОТОКОВ:
====================
- RobotThread: основной поток управления роботом (сканирование + сортировка)
- LISRequestThread: поток параллельных запросов к ЛИС

Все потоки инициализируются здесь и связываются через PipelineContext.
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
from src.eppendorf_sorter.orchestration.robot_logic import (
    RobotThread,
    LISRequestThread,
    PipelineContext,
)
from src.eppendorf_sorter.orchestration.shutdown import shutdown


# Загрузка конфигурации
ROBOT_CFG = load_robot_config()
ROBOT_SCANNER = ROBOT_CFG.scanner
ROBOT_LIS = ROBOT_CFG.lis


def build_loggers() -> Dict[str, logging.Logger]:
    """Создаёт логгеры для различных компонентов системы.

    Returns:
        Словарь с именованными логгерами. Ключ ``"robot"`` содержит
        логгер основного потока робота.
    """
    logger_robot = create_logger("ProjectR.Robot", "robot.log")

    return {
        "robot": logger_robot,
    }


def initialize_source_racks() -> List[SourceRack]:
    """Инициализирует исходные штативы (паллеты) для сканирования.

    Returns:
        Список настроенных SourceRack (2 паллета).
    """
    source_racks = [
        SourceRack(pallet_id=1),  # П0
        SourceRack(pallet_id=2),  # П1
    ]

    return source_racks


def initialize_destination_racks() -> List[DestinationRack]:
    """Инициализирует целевые штативы для размещения пробирок.

    Структура:
    - Rack 0: OTHER (разное)
    - Rack 1-2: UGI (pcr-1)
    - Rack 3-4: VPCH (pcr-2)
    - Rack 5-6: UGI_VPCH (оба теста)

    Returns:
        Список настроенных DestinationRack (7 штативов).
    """
    destination_racks = [
        # Rack 0: Разное (OTHER)
        DestinationRack(
            rack_id=3,
            test_type=TestType.OTHER,
            target=50
        ),

        # Rack 1-2: UGI (pcr-1)
        DestinationRack(
            rack_id=4,
            test_type=TestType.UGI,
            target=50
        ),
        DestinationRack(
            rack_id=5,
            test_type=TestType.UGI,
            target=50
        ),

        # Rack 3-4: VPCH (pcr-2)
        DestinationRack(
            rack_id=6,
            test_type=TestType.VPCH,
            target=50
        ),
        DestinationRack(
            rack_id=7,
            test_type=TestType.VPCH,
            target=50
        ),

        # Rack 5-6: UGI_VPCH (оба теста)
        DestinationRack(
            rack_id=8,
            test_type=TestType.UGI_VPCH,
            target=50
        ),
        DestinationRack(
            rack_id=9,
            test_type=TestType.UGI_VPCH,
            target=50
        ),
    ]

    return destination_racks


def initialize_rack_system_manager(logger: logging.Logger) -> RackSystemManager:
    """Инициализирует RackSystemManager с исходными и целевыми штативами.

    Args:
        logger: Логгер для вывода информации о ходе инициализации.

    Returns:
        Настроенный RackSystemManager с загруженными паллетами и штативами.
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
    """Инициализирует и подключает робота и сканер.

    При ошибке подключения к любому из устройств выполняется
    отключение уже подключённых устройств перед проброской исключения.

    Args:
        config: Конфигурация робота (содержит параметры подключения).
        logger: Логгер для вывода информации о подключении.

    Returns:
        Кортеж (robot, scanner) - подключённые устройства.

    Raises:
        Exception: При ошибке подключения к роботу или сканеру.
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


def initialize_pipeline_context(stop_event: threading.Event, logger: logging.Logger) -> PipelineContext:
    """Инициализирует контекст для межпоточного взаимодействия.

    Args:
        stop_event: Глобальное событие остановки, разделяемое между потоками.
        logger: Логгер для вывода информации.

    Returns:
        Настроенный PipelineContext с привязанным stop_event.
    """
    context = PipelineContext(stop_event=stop_event)
    logger.info("✓ PipelineContext инициализирован")
    return context


def initialize_lis_thread(
    context: PipelineContext,
    config,
    logger: logging.Logger
) -> LISRequestThread:
    """Инициализирует поток запросов к ЛИС.

    Создаёт экземпляр LISRequestThread, но не запускает его.
    Запуск выполняется отдельно в ``run_workcell()``.

    Args:
        context: Контекст межпоточного взаимодействия с очередями и событиями.
        config: Конфигурация робота (содержит LIS-настройки: ip, port).
        logger: Логгер для вывода информации.

    Returns:
        Настроенный LISRequestThread (не запущенный).
    """
    lis_thread = LISRequestThread(
        context=context,
        lis_host=config.lis.ip,
        lis_port=config.lis.port,
        logger=logger,
        max_workers=10,
    )
    logger.info(f"✓ LISRequestThread инициализирован (host={config.lis.ip}:{config.lis.port}, workers=10)")
    return lis_thread


def run_workcell():
    """Главная функция запуска системы сортировки.

    Выполняет полный цикл инициализации и запуска:
    1. Создание логгеров и установка глобальных хуков исключений.
    2. Инициализация устройств (робот, сканер).
    3. Инициализация RackSystemManager.
    4. Создание и запуск потоков LISRequestThread и RobotThread.
    5. Ожидание Ctrl+C для корректного завершения.

    При получении KeyboardInterrupt выполняется процедура shutdown:
    остановка потоков, отключение устройств, освобождение ресурсов.
    """
    # --- Создание объекта для управления потоками ---
    stop_event = threading.Event()

    # --- Логгеры + хуки ---
    loggers = build_loggers()
    install_global_exception_hooks()

    loggers["robot"].info("="*60)
    loggers["robot"].info("ЗАПУСК СИСТЕМЫ СОРТИРОВКИ ПРОБИРОК")
    loggers["robot"].info("="*60)

    # --- Загрузка конфигурации ---
    config = ROBOT_CFG
    loggers["robot"].info("\nКонфигурация загружена:")
    loggers["robot"].info(f"  - Робот: {config.name} ({config.ip})")
    loggers["robot"].info(f"  - Сканер: {config.scanner.name} ({config.scanner.ip}:{config.scanner.port})")
    loggers["robot"].info(f"  - ЛИС: {config.lis.ip}:{config.lis.port}")

    # --- Инициализация устройств ---
    try:
        robot, scanner = initialize_devices(config, loggers["robot"])
    except Exception as e:
        loggers["robot"].error(f"\n❌ Не удалось инициализировать устройства: {e}")
        loggers["robot"].error("Система не может быть запущена")
        return

    # --- Инициализация RackSystemManager ---
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

    # --- Инициализация LIS клиента ---
    lis_client = LISClient(
        host=config.lis.ip,
        port=config.lis.port,
        max_workers=30
    )
    loggers["robot"].info("✓ LIS клиент инициализирован (workers=30)")

    # --- Инициализация PipelineContext ---
    loggers["robot"].info("\nИнициализация Pipeline...")
    context = initialize_pipeline_context(stop_event, loggers["robot"])

    # --- Инициализация LISRequestThread ---
    lis_thread = initialize_lis_thread(context, config, loggers["robot"])

    # --- Создание главного потока обработки (RobotThread) ---
    loggers["robot"].info("\nСоздание главного потока обработки...")
    robot_thread = RobotThread(
        rack_manager=rack_manager,
        robot=robot,
        scanner=scanner,
        lis_client=lis_client,
        context=context,
        lis_thread=lis_thread,
        logger=loggers["robot"],
        stop_event=stop_event
    )
    loggers["robot"].info("✓ RobotThread инициализирован")

    # --- Сбор и запуск потоков ---
    threads: list[threading.Thread] = [
        robot_thread,
        lis_thread,
    ]

    loggers["robot"].info("\nЗапуск потоков...")

    lis_thread.start()
    loggers["robot"].info("✓ LISRequestThread запущен")

    robot_thread.start()
    loggers["robot"].info("✓ RobotThread запущен")

    loggers["robot"].info("\n" + "="*60)
    loggers["robot"].info("✓ СИСТЕМА ЗАПУЩЕНА И ГОТОВА К РАБОТЕ")
    loggers["robot"].info("="*60 + "\n")

    # --- Основной цикл ожидания ---
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
        # --- Процедура корректного завершения ---
        loggers["robot"].info("\nНачало процедуры остановки...")

        # Останавливаем LISRequestThread executor
        loggers["robot"].info("Остановка LISRequestThread executor...")
        try:
            lis_thread.shutdown()
            loggers["robot"].info("✓ LISRequestThread executor остановлен")
        except Exception as e:
            loggers["robot"].error(f"❌ Ошибка остановки LISRequestThread executor: {e}")

        # Общий shutdown потоков
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
