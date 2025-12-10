# src/eppendorf_sorter/orchestration/loader_logic.py
import time
import threading
import logging
from dataclasses import dataclass

from src.eppendorf_sorter.devices import CellRobot, Scanner
from src.eppendorf_sorter.config.loader_config import load_loader_config
from src.eppendorf_sorter.domain import (
    RackManager, 
    RackOccupancy,
)

@dataclass(frozen=True)
class LoaderNRNumbers:
    iteration_starter: int = 1
    grip_status: int = 2
    scan_status: int = 3
    scan_delay: int = 4
    move_status: int = 5

@dataclass(frozen=True)
class LoaderNRValues:
    start: int = 1
    reset: int = 0
    end: int = 2
    grip_good: int = 1
    grip_bad: int = 2
    grip_reset: int = 0
    scan_good: int = 1
    scan_bad: int = 2
    scan_reset: int = 0
    delay_reset: float = 0.0
    move_start: int = 1
    move_stop: int = 0

@dataclass(frozen=True)
class LoaderSRNumbers:
    iteration_type: int = 1
    loading_data: int = 2
    
@dataclass(frozen=True)
class LoaderSRValues:
    loading: str = "LOADING_ITERATION"
    stacking: str = "STACKING_ITERATION"
    breaking: str = "BREAK_ITERATION"
    none: str = "NONE"

LOADER_CFG = load_loader_config()
SCANNER = LOADER_CFG.scanner

LOADER_NR_NUMBERS = LoaderNRNumbers()
LOADER_NR_VALUES = LoaderNRValues()

LOADER_SR_NUMBERS = LoaderSRNumbers()
LOADER_ITERATION_NAMES = LoaderSRValues()

class LoaderRobotThread(threading.Thread):
    """
    Поток, выполняющий основную логику робота-загрузчика.

    Остановка: через stop_event (threading.Event).
    Пока stop_event не выставлен, поток крутит свой цикл.
    """

    def __init__(
        self,
        rack_manager: RackManager,
        loader_robot: CellRobot,
        loader_scanner: Scanner,
        loader_tripods: dict[str, UnloadingTripod],   # из этих штативов пробирки забираются
        unloader_tripods: dict[str, LoadingTripod],   # в эти штативы пробирки ставятся
        loader_tripods_monitor: TripodMonitor,
        logger: logging.Logger,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="LoaderRobotThread", daemon=True)
        self.rack_manager = rack_manager
        self.loader_robot = loader_robot
        self.loader_scanner = loader_scanner
        self.loader_tripods = loader_tripods
        self.unloader_tripods = unloader_tripods
        self.loader_tripods_monitor = loader_tripods_monitor
        self.logger = logger
        self.stop_event = stop_event

    def _wait_until(self, condition, poll: float = 0.1) -> bool:
        """
        Ждём, пока condition() вернёт True или пока stop_event не будет выставлен.
        Возвращает True, если условие выполнилось, False если нас прервали.
        """
        while not self.stop_event.is_set():
            if condition():
                return True
            time.sleep(poll)
        return False

    def run(self) -> None:
        self.logger.info("[Loader] Поток запущен")
        
        try:
            # 0. Подготовка робота
            self.loader_robot.stop_all_running_programms()
            self.loader_robot.reset_errors()
            self.loader_robot.start_program(LOADER_CFG.robot_program_name)

            while not self.stop_event.is_set():
                # 1. Определяем основные параемтры для определения типа итерации
                loader_available_tripod = self.loader_tripods_monitor.get_available_tripod_name()        # Нахождение доступного трипода
                full_rack_info = self.rack_manager.check_full_rack_in_loader_zone()                      # Проверка наличия полных рэков 
                partially_full_rack_info = self.rack_manager.get_partially_filled_rack_in_loader_zone()  # Проверка наличия частично заполненного рэка
                tubes_in_mindray = self.rack_manager.get_total_tubes_in_mindray()                        # Подсчет пробирок в миндрее
                tubes_in_unloader_rack_zone = self.rack_manager.get_total_tubes_in_unloader_zone()       # Подсчет пробирок в рэках для выгрузки
                available_places_in_unloader_tripods = sum(                                              # Подсчет пробирок штативах для выгрузки
                    tripod.get_empty_places() 
                    for tripod in self.unloader_tripods.values() 
                    if tripod.availability
                )

                # 2. Определяем тип итерации на основании теперь известных параметров
                if full_rack_info and not (tubes_in_mindray >= available_places_in_unloader_tripods - tubes_in_unloader_rack_zone):
                    position, rack_to_mindray_amount = full_rack_info
                    self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.stacking)
                    current_iteration_type = LOADER_ITERATION_NAMES.stacking
                    self.logger.info("Найден заполненный рэк!\n")
                elif not loader_available_tripod and partially_full_rack_info and not (tubes_in_mindray >= available_places_in_unloader_tripods - tubes_in_unloader_rack_zone):
                    position = partially_full_rack_info
                    rack_to_mindray_amount = 5
                    self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.stacking)
                    current_iteration_type = LOADER_ITERATION_NAMES.stacking
                    self.logger.info("Найден не до конца заполненный рэк при недоступности триподов выгрузки!\n")
                elif loader_available_tripod and self.rack_manager.has_available_racks_in_loader_zone():
                    self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.loading)
                    current_iteration_type = LOADER_ITERATION_NAMES.loading
                    self.logger.info("Заполняем рэк пробирками!\n")
                else:
                    current_iteration_type = LOADER_ITERATION_NAMES.none

                
                # 3. Логика итерации загрузки пробирок в рэки
                if current_iteration_type == LOADER_ITERATION_NAMES.loading:
                    if not loader_available_tripod:
                        self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.none)
                        self.logger.warning("Штатив был убран во время работы установки")
                        continue

                    # 3.1. В цикле разбираем пробикри до заполнения рэка
                    for tube in range(self.loader_tripods[loader_available_tripod].get_tubes()):
                        self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.loading) 
                        self.logger.info("\n ====LOADING ITERATION====\n")

                        # 3.2. Проверяем появился ли заполенный рэк и можно ли его ставить в миндрей
                        tubes_in_mindray = self.rack_manager.get_total_tubes_in_mindray()                        # Подсчет пробирок в миндрее
                        tubes_in_unloader_rack_zone = self.rack_manager.get_total_tubes_in_unloader_zone()       # Подсчет пробирок в рэках для выгрузки
                        available_places_in_unloader_tripods = sum(                                         # Подсчет пробирок штативах для выгрузки
                            tripod.get_empty_places() 
                            for tripod in self.unloader_tripods.values() 
                            if tripod.availability
                        )
                        full_rack_info = self.rack_manager.check_full_rack_in_loader_zone()
                        if full_rack_info and not (tubes_in_mindray >= available_places_in_unloader_tripods - tubes_in_unloader_rack_zone):
                            self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.stacking)
                            break

                        # 3.3. Определяем точки назначения робота
                        try: 
                            tripod_tube_num = self.loader_tripods[loader_available_tripod].grab_tube()           # Номер пробирки в штативе
                        except Exception as e:
                            self.logger.warning("Штатив был убран во время работы установки")
                            continue
                        rack_info = self.rack_manager.get_nearest_available_rack_in_loader_zone()                # Номер ближайшего доступного пустого рэка
                        partially_full_rack_info = self.rack_manager.get_partially_filled_rack_in_loader_zone()  # Номер ближайшего доступного неполного рэка
                        if rack_info:                                                                       # Если нашли доступный рэк
                            rack_number = rack_info                                                   # Номер пустого дотсупного рэка
                            if partially_full_rack_info:
                                rack_number = partially_full_rack_info                                # Номер непустого дотсупного рэка
                            self.rack_manager.occupy_racks_by_robot(                                             # Окуупируем ближайшие рэки 
                                position=rack_number,  
                                busyness=RackOccupancy.BUSY_LOADER, 
                                release=False,
                                logger=self.logger
                            )
                            rack_tube_number = self.rack_manager.get_rack_tube_count(rack_number)                # Номер пробирки в рэке
                            data_str = (                                                # Формируем пакет данных в виде строки роботу
                                f"{int(loader_available_tripod):02d} "
                                f"{tripod_tube_num:02d} "
                                f"{int(rack_number):02d} "
                                f"{rack_tube_number:02d}"
                            )
                            self.loader_robot.set_string_register(LOADER_SR_NUMBERS.loading_data, data_str)        # Отправляем роботу строку с данными
                        else:
                            self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.none)
                            break

                        # 3.4. Стартуем итерацию после отправки всех данных роботу
                        self.loader_robot.set_number_register(LOADER_NR_NUMBERS.iteration_starter, LOADER_NR_VALUES.start) 
                        self.logger.info(f"Отдана команда на исполенние итерации {current_iteration_type}!") 

                        # 3.5. Ждем ответа от робота - есть ли пробирка в штатвие?
                        if not self._wait_until(
                            lambda: self.loader_robot.get_number_register(
                                LOADER_NR_NUMBERS.grip_status
                            )
                            != LOADER_NR_VALUES.grip_reset
                        ):
                            break

                        # 3.5.1. Если пробирка есть
                        if self.loader_robot.get_number_register(LOADER_NR_NUMBERS.grip_status) == LOADER_NR_VALUES.grip_good:
                            pass                                                                                            # Просто идем дальше             
                        # 3.5.2. Если пробирки нет
                        elif self.loader_robot.get_number_register(LOADER_NR_NUMBERS.grip_status) == LOADER_NR_VALUES.grip_bad:
                            self.logger.warning(f"В штативе нет пробирок! ---> Производится обнуление...")                     
                            self.loader_tripods[loader_available_tripod].set_availability(False)
                            self.loader_tripods[loader_available_tripod].set_tubes(Tripod.MIN_TUBES)
                            self.logger.warning("Штатив обнулен!")
                            self.loader_robot.set_string_register(LOADER_SR_NUMBERS.iteration_type, LOADER_ITERATION_NAMES.none)
                            self.logger.warning("Итерация прервана!")
                            break
                        # 3.5.3 Сбрасываем для следующей итерации
                        self.loader_robot.set_number_register(LOADER_NR_NUMBERS.grip_status, LOADER_NR_VALUES.grip_reset)        # Сбрасываем для следующей итерации

                        # 3.6 Сканируем и ориентрируем пробикру
                        self.logger.info(f"Ожидание команды сканирвоания...")
                        if not self._wait_until(
                            lambda: self.loader_robot.get_number_register(
                               LOADER_NR_NUMBERS.scan_status 
                            )
                            != LOADER_NR_VALUES.scan_reset
                        ):
                            break
                        self.logger.info(f"Команда на сканирование получена!")
                        barcode, delay = self.loader_scanner.scan(timeout=SCANNER.timeout)
                        # 3.6.1. Если смогли считать штриход - ставим пробирку в рэк
                        if barcode != "NoRead":
                            self.loader_robot.set_number_register(LOADER_NR_NUMBERS.scan_delay, delay)
                            self.loader_robot.set_number_register(LOADER_NR_NUMBERS.scan_status, LOADER_NR_VALUES.scan_good)
                            self.rack_manager.add_tube_to_rack(rack_number, barcode)
                        # 3.6.2. Если смогли считать штриход - возваращаем пробирку обртано в штатив на то же место
                        else:
                            self.loader_robot.set_number_register(LOADER_NR_NUMBERS.scan_status, LOADER_NR_VALUES.scan_bad)
                        # 3.6.3. Сбрасываем для следующей итерации
                        self.loader_robot.set_number_register(LOADER_NR_NUMBERS.scan_status, LOADER_NR_VALUES.scan_reset)
                        time.sleep(0.2)
                        self.loader_robot.set_number_register(LOADER_NR_NUMBERS.scan_delay, LOADER_NR_VALUES.delay_reset)
                        
                        # 3.7. Ждем инофрмации о завершении итерации роботом
                        self.logger.info(f"Ожидание команды на завершение итерации...")
                        if not self._wait_until(
                            lambda: self.loader_robot.get_number_register(
                                LOADER_NR_NUMBERS.iteration_starter
                            )
                            == LOADER_NR_VALUES.end
                        ):
                            break
                        self.logger.info(f"Команда на завершение итерации получена!")

                        # 3.8. Возваращем стутус "FREE" ранее оккупированным рэкам
                        self.rack_manager.occupy_racks_by_robot(
                            position=rack_number, 
                            busyness=RackOccupancy.BUSY_LOADER, 
                            release=True, 
                            logger=self.logger
                        )

                        # 3.9 Логируем состояние рэка и штатива по окночанию итерации
                        self.rack_manager.log_rack_info(rack_number, self.logger)
                        self.logger.info(self.loader_tripods[loader_available_tripod])


                # 4. Логика итерации загрузки рэков м миндрей
                elif current_iteration_type == LOADER_ITERATION_NAMES.stacking:

                    # 4.1. Оккупируем ближайшие рэки
                    self.rack_manager.occupy_racks_by_robot(
                        position=position, 
                        busyness=RackOccupancy.BUSY_LOADER, 
                        release=False, 
                        logger=self.logger
                    )

                    # 4.2. Определяем точки назанчения робота 
                    data_str = (                                                                # Формируем пакет данных в виде строки роботу
                        f"{int(rack_to_mindray_amount):02d} "                                   # Количесвто рэков на постановку                                                           
                        f"{int(position):02d}"                                                  # Номер рэка 
                    )
                    self.loader_robot.set_string_register(LOADER_SR_NUMBERS.loading_data, data_str)  # Отправляем роботу строку с данными

                    # 4.3. Стартуем итерацию после отправки всех данных роботу
                    self.loader_robot.set_number_register(LOADER_NR_NUMBERS.iteration_starter, LOADER_NR_VALUES.start) 
                    self.logger.info(f"Отдана команда на исполенние итерации {current_iteration_type}!")

                    # 4.4. Обновляем рэк мэнэджер, убирая рэк в миндрей
                    self.rack_manager.move_rack_to_mindray(position, self.logger)
                    self.logger.info(f"Рэк {position} перемещен в MindRay")

                    # 4.5. Делаем рэки доступными после ухода робота из зоны загрузки
                    self.rack_manager.occupy_racks_by_robot(position=position, busyness=RackOccupancy.BUSY_LOADER, release=True, logger=self.logger)

                    # ====== ВАЖНО ======
                    # Код робота должен нажимать на кнопку по при rack_to_mindray_amount = 1, иначе рэки никогда не будут загружены
                    # TODO выяснить временной алгоритм блока загрузки миндрея

                    # 4.6. Ждем инофрмации о завершении итерации роботом
                    self.logger.info(f"Ожидание команды на завершение итерации...")
                    if not self._wait_until(
                        lambda: self.loader_robot.get_number_register(
                            LOADER_NR_NUMBERS.iteration_starter
                        )
                        == LOADER_NR_VALUES.end
                    ):
                        break
                    self.logger.info(f"Команда на завершение итерации получена!")

                    # 4.7 Сбрасываем флаг готовности робота двигаться дальше
                    self.loader_robot.set_number_register(LOADER_NR_NUMBERS.move_status, LOADER_NR_VALUES.move_stop)
                    # Этот флаг подниамет другой робот после того как завершит процедурур перестанвоки путсого рэка
                    # Код робота ждет поднятия этого флага и не дваигается до его получения
                
                    # 4.8. Логгируем состояние всех рэков после постановки рэка в миндрей
                    self.logger.info(self.rack_manager.get_system_status())


                #Время между иетрациями основного цикла
                time.sleep(0.1)

        except Exception as e:
            self.logger.fatal(f"Ошибка: {e}")
