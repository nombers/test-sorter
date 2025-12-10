# src/eppendorf_sorter/orchestration/bootstrap.py
import threading
import logging
import time
from typing import Dict

from src.eppendorf_sorter.logging import (
    create_logger, 
    install_global_exception_hooks,
)

from src.eppendorf_sorter.config import (
    load_system_layout_config,
    load_loader_config,
)
from src.eppendorf_sorter.devices import (
    CellRobot,
    Scanner,
    RobotAgilebot,
    ScannerHikrobotTCP,
)
from src.eppendorf_sorter.domain import (
    RackManager,
)
from src.eppendorf_sorter.orchestration.loader_logic import LoaderRobotThread
from src.eppendorf_sorter.orchestration.shutdown import shutdown


LOADER_CFG = load_loader_config()
LOADER_SCANNER = LOADER_CFG.scanner


def build_loggers():
    logger_loader = create_logger("ProjectR.Loading", "loader_robot.log")
    logger_gui = create_logger("ProjectR.GUI", "GUI.log")

    return {
        "loader": logger_loader,
        "gui": logger_gui,
    }

def build_layout(logger: logging.Logger):
    cfg = load_system_layout_config()

    unloading_tripods = [
        UnloadingTripod(name=f"{i+1}") 
        for i in range(cfg.unloading_tripods)
    ]
    loading_tripods = [
        LoadingTripod(name=f"{i+1}") 
        for i in range(cfg.loading_tripods)
    ]

    rack_manager = RackManager(
        racks_in_loading_zone=cfg.racks_in_loading_zone,
        racks_in_unloading_zone=cfg.racks_in_unloading_zone,
    )

    return unloading_tripods, loading_tripods, rack_manager

def build_tripod_monitor(
    tripods: list[Tripod],
    sensor_prefix: str,
    robots: Dict[RobotRole, CellRobot],
    logger: logging.Logger,
    thread_name: str,
    stop_event: threading.Event,
    debounce_seconds: float = 2.0,
    poll_interval: float = 0.1,
) -> tuple[Dict[str, Tripod], TripodMonitor]:
    """
    Строит словарь триподов и монитор для них.

    tripods       — список объектов Tripod (LoadingTripod или UnloadingTripod)
    sensor_prefix — префикс имени датчиков из sensors.yaml, например:
                    "loader_pallet_" или "unloader_pallet_"
    """
    # превращаем список триподов в словарь по имени: "1" -> tripod
    tripod_map: Dict[str, Tripod] = {t.name: t for t in tripods}

    all_sensors = load_sensors_config()
    tripod_sensors: Dict[str, SensorConfig] = {}

    for sensor in all_sensors:
        if sensor.name.startswith(sensor_prefix):
            # loader_pallet_1_present -> ["loader", "pallet", "1", "present"]
            parts = sensor.name.split("_")
            if len(parts) >= 3:
                tripod_name = parts[2]   # "1", "2", "3", ...
                if tripod_name in tripod_map:
                    tripod_sensors[tripod_name] = sensor

    monitor = TripodMonitor(
        tripods=tripod_map,
        tripod_sensors=tripod_sensors,
        robots=robots,
        logger=logger,
        stop_event=stop_event,
        debounce_seconds=debounce_seconds,
        poll_interval=poll_interval,
    )
    monitor.name = thread_name
    monitor.daemon = True
    monitor.start()

    return tripod_map, monitor


def run_workcell():
    # 0. Создаем объекты для управления потоками
    stop_event = threading.Event()

    # 1. Поднимаем роботов
    loader_robot = RobotAgilebot(name=LOADER_CFG.name, ip=LOADER_CFG.ip)
    loader_scanner = ScannerHikrobotTCP(
        name=LOADER_SCANNER.name, 
        ip=LOADER_SCANNER.ip, 
        port=LOADER_SCANNER.port
    )
    # unloader_robot = RobotAgilebot(name="UnloaderRobot", ip="192.168.124.3")

    loader_robot.connect()
    # unloader_robot.connect()

    robots: dict[RobotRole, CellRobot] = {
        RobotRole.LOADER: loader_robot,
        # RobotRole.UNLOADER: unloader_robot,
    }

    # 2. Логгеры + хуки
    loggers = build_loggers()
    install_global_exception_hooks()


    # 3. Геометрия системы (штативы, рэки и т.д.)
    unloading_tripods_list, loading_tripods_list, rack_manager = build_layout(
        logger=loggers["loader"]
    )

    # 4. Монитор триподов для первого робота (loader)
    loader_tripods_by_name, loader_tripod_monitor = build_tripod_monitor(
        tripods=unloading_tripods_list,
        sensor_prefix="loader_pallet_",
        robots=robots,
        logger=loggers["loader"],
        thread_name="LoaderTripodMonitor",
        stop_event=stop_event
    )

    # 5. Монитор триподов для второго робота (unloader)
    unloader_tripods_by_name, unloader_tripod_monitor = build_tripod_monitor(
        tripods=loading_tripods_list,
        sensor_prefix="unloader_pallet_",
        robots=robots,
        logger=loggers["unloader"],
        thread_name="UnloaderTripodMonitor",
        stop_event=stop_event
    )

    # 6. Тестовое наблюдение (чисто для дебага)
    import time
    alarms_manager.trigger_alarm("CRITICAL LOADER ERROR", "test1")
    # while True:
    #     loader_available = loader_tripod_monitor.get_available_tripod_name()
    #     unloader_available = unloader_tripod_monitor.get_available_tripod_name()
    #     print(f"LOADER tripod: {loader_available} | UNLOADER tripod: {unloader_available}")
    #     if loader_available:
    #         print(loader_tripods_by_name[str(loader_available)])
    #     if unloader_available:
    #         print(unloader_tripods_by_name[str(unloader_available)])

    #     available_places_in_unloader_tripods = sum(
    #             tripod.get_tubes() 
    #             for tripod in loader_tripods_by_name.values() 
    #             if tripod.availability
    #         )
    #     print(available_places_in_unloader_tripods)

    #     time.sleep(0.5)

    # 7. Поток логики загрузчика
    loader_thread = LoaderRobotThread(
        rack_manager=rack_manager,
        loader_robot=loader_robot,
        loader_scanner=loader_scanner,
        loader_tripods=loader_tripods_by_name,
        unloader_tripods=unloader_tripods_by_name,
        loader_tripods_monitor=loader_tripod_monitor,
        logger= loggers["loader"],
        stop_event=stop_event,
    )
    loader_thread.start()


    # 8. Собираем все потоки, которыми управляем
    threads: list[threading.Thread] = [
        loader_tripod_monitor,
        unloader_tripod_monitor,
        loader_thread,
        alarms_manager,
    ]


    # 9. Основной цикл / ожидание (пока просто живём)
    try:
        loggers["loader"].info("Рабочая ячейка запущена")
        # Примитивный вариант: просто ждём, пока нас не убьют Ctrl+C
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        loggers["loader"].info("Получен KeyboardInterrupt, инициируем остановку...")
    finally:
        # 10. Аккуратный shutdown
        shutdown(stop_event=stop_event, threads=threads, logger=loggers["loader"])

        # Гасим роботов/сканеры
        try:
            loader_robot.stop_all_running_programms()
            loader_robot.disconnect()
        except Exception as e:
            loggers["loader"].error(f"Ошибка при отключении loader_robot: {e}")

        loggers["loader"].info("run_workcell завершён")