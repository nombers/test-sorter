# src/eppendorf_sorter/orchestration/bootstrap_gui.py
"""Модуль запуска системы сортировки с GUI интерфейсом.

Интегрирует tkinter GUI с потоками обработки (RobotThread, LISRequestThread).
Предоставляет GUIController для связи пользовательского интерфейса
с бизнес-логикой сортировки.
"""
import tkinter as tk
import threading
import logging
import time
from typing import Optional

from src.eppendorf_sorter.logging import (
    create_logger,
    install_global_exception_hooks,
)

from src.eppendorf_sorter.config import load_robot_config

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
from src.eppendorf_sorter.gui import SorterGUI


# Загрузка конфигурации
ROBOT_CFG = load_robot_config()


class GUIController:
    """Контроллер для связи GUI с потоками обработки робота.

    Обеспечивает полный жизненный цикл системы через GUI:
    инициализацию компонентов, запуск/остановку/паузу потоков,
    а также периодическое обновление статистики ЛИС в интерфейсе.

    Attributes:
        gui: Экземпляр SorterGUI для взаимодействия с пользователем.
        logger: Логгер для записи событий контроллера.
    """

    def __init__(self, gui: SorterGUI, logger: logging.Logger):
        """Инициализирует контроллер и привязывает обработчики GUI.

        Args:
            gui: Экземпляр графического интерфейса SorterGUI.
            logger: Логгер для записи событий.
        """
        self.gui = gui
        self.logger = logger

        # Состояние
        self._stop_event = threading.Event()
        self._is_running = False
        self._is_paused = False
        self._gui_initiated_pause = False  # Пауза была инициирована из GUI

        # Компоненты (инициализируются при старте)
        self._robot: Optional[CellRobot] = None
        self._scanner: Optional[Scanner] = None
        self._rack_manager: Optional[RackSystemManager] = None
        self._lis_client: Optional[LISClient] = None
        self._context: Optional[PipelineContext] = None
        self._lis_thread: Optional[LISRequestThread] = None
        self._robot_thread: Optional[RobotThread] = None

        # Регистрируем callbacks в GUI
        gui.set_start_callback(self._on_start)
        gui.set_stop_callback(self._on_stop)
        gui.set_pause_callback(self._on_pause)
        gui.set_resume_callback(self._on_resume)
        gui.set_rack_replaced_callback(self._on_rack_replaced)

    def _initialize_rack_manager(self) -> RackSystemManager:
        """Создаёт и настраивает менеджер штативов.

        Returns:
            Инициализированный RackSystemManager с паллетами и целевыми штативами.
        """
        source_racks = [
            SourceRack(pallet_id=1),
            SourceRack(pallet_id=2),
        ]

        # rack_id соответствует bootstrap.py:
        # 3: OTHER, 4-5: UGI, 6-7: VPCH, 8-9: UGI_VPCH
        destination_racks = [
            DestinationRack(rack_id=3, test_type=TestType.OTHER, target=50),
            DestinationRack(rack_id=4, test_type=TestType.UGI, target=50),
            DestinationRack(rack_id=5, test_type=TestType.UGI, target=50),
            DestinationRack(rack_id=6, test_type=TestType.VPCH, target=50),
            DestinationRack(rack_id=7, test_type=TestType.VPCH, target=50),
            DestinationRack(rack_id=8, test_type=TestType.UGI_VPCH, target=50),
            DestinationRack(rack_id=9, test_type=TestType.UGI_VPCH, target=50),
        ]

        rack_manager = RackSystemManager()
        rack_manager.initialize_system(
            pallets_list=source_racks,
            racks_list=destination_racks
        )

        return rack_manager

    def _initialize_devices(self) -> tuple[CellRobot, Scanner]:
        """Инициализирует и подключает робота и сканер.

        Returns:
            Кортеж (robot, scanner) - подключённые устройства.

        Raises:
            Exception: При ошибке подключения к роботу или сканеру.
        """
        config = ROBOT_CFG

        robot = RobotAgilebot(
            name=config.name,
            ip=config.ip
        )

        scanner = ScannerHikrobotTCP(
            name=config.scanner.name,
            ip=config.scanner.ip,
            port=config.scanner.port
        )

        robot.connect()
        self.logger.info("Робот подключен")

        scanner.connect()
        self.logger.info("Сканер подключен")

        return robot, scanner

    def _on_start(self):
        """Обработчик нажатия кнопки «Старт» в GUI.

        Инициализирует все компоненты системы, создаёт и запускает
        рабочие потоки. При ошибке выполняет очистку ресурсов.

        Raises:
            Exception: При ошибке инициализации любого компонента
                (пробрасывается после очистки ресурсов).
        """
        if self._is_running:
            return

        self.logger.info("Запуск системы...")

        try:
            # --- Сброс stop_event ---
            self._stop_event.clear()

            # --- Сохранение целевых значений из GUI ---
            saved_targets = {}
            for rack_id, target_var in self.gui._rack_target_vars.items():
                try:
                    saved_targets[rack_id] = int(target_var.get())
                except ValueError:
                    saved_targets[rack_id] = 50  # По умолчанию

            # --- Инициализация менеджера штативов ---
            self._rack_manager = self._initialize_rack_manager()

            # Применяем сохранённые целевые значения
            for rack_id, target in saved_targets.items():
                rack = self._rack_manager.get_destination_rack(rack_id)
                if rack:
                    rack.set_target(target)
                    self.logger.debug(f"Применено целевое значение {target} для штатива #{rack_id}")

            self.gui.set_rack_manager(self._rack_manager)
            self.logger.info("RackSystemManager инициализирован")

            # --- Инициализация устройств ---
            self._robot, self._scanner = self._initialize_devices()

            # --- LIS клиент ---
            config = ROBOT_CFG
            self._lis_client = LISClient(
                host=config.lis.ip,
                port=config.lis.port,
                max_workers=5
            )
            self.logger.info("LIS клиент инициализирован")

            # --- Pipeline контекст ---
            self._context = PipelineContext(stop_event=self._stop_event)
            self.logger.info("PipelineContext инициализирован")

            # --- LIS поток ---
            self._lis_thread = LISRequestThread(
                context=self._context,
                lis_host=config.lis.ip,
                lis_port=config.lis.port,
                logger=self.logger,
                max_workers=10,
            )
            self.logger.info("LISRequestThread инициализирован")

            # --- Robot поток ---
            self._robot_thread = RobotThread(
                rack_manager=self._rack_manager,
                robot=self._robot,
                scanner=self._scanner,
                lis_client=self._lis_client,
                context=self._context,
                lis_thread=self._lis_thread,
                logger=self.logger,
                stop_event=self._stop_event
            )

            # --- Подмена callbacks для режима ожидания ---
            original_enter = self._robot_thread._enter_waiting_mode
            original_exit = self._robot_thread._exit_waiting_mode

            def enter_waiting_wrapper(reason: str):
                original_enter(reason)
                # Если пауза была инициирована из GUI, не меняем статус на WAITING
                # (GUI уже показывает PAUSED)
                if not self._gui_initiated_pause:
                    self.gui.root.after(0, lambda: self.gui.set_waiting_mode(reason))

            def exit_waiting_wrapper():
                original_exit()
                # Если была пауза из GUI, статус вернётся в RUNNING через _on_resume
                # Если это было ожидание штативов, возвращаем в RUNNING здесь
                if not self._gui_initiated_pause:
                    self.gui.root.after(0, self.gui.exit_waiting_mode)

            self._robot_thread._enter_waiting_mode = enter_waiting_wrapper
            self._robot_thread._exit_waiting_mode = exit_waiting_wrapper

            self.logger.info("RobotThread инициализирован")

            # --- Запуск потоков ---
            self._lis_thread.start()
            self.logger.info("LISRequestThread запущен")

            self._robot_thread.start()
            self.logger.info("RobotThread запущен")

            self._is_running = True

            # Запуск обновления статистики ЛИС
            self._start_lis_stats_update()

            self.logger.info("Система запущена и готова к работе")

        except Exception as e:
            self.logger.error(f"Ошибка запуска: {e}")
            self._cleanup()
            raise

    def _on_stop(self):
        """Обработчик нажатия кнопки «Стоп» в GUI.

        Запрашивает остановку у RobotThread (робот уходит в home),
        устанавливает stop_event и ожидает завершения потоков.
        """
        if not self._is_running:
            return

        self.logger.info("Остановка системы (робот уйдёт в home)...")

        # Сначала запрашиваем остановку у робота (он уйдёт в home)
        if self._robot_thread:
            self._robot_thread.request_stop()

        # Устанавливаем stop_event
        self._stop_event.set()

        # Ожидаем завершения потоков (даём больше времени чтобы робот успел уехать в home)
        if self._robot_thread and self._robot_thread.is_alive():
            self._robot_thread.join(timeout=15.0)

        if self._lis_thread and self._lis_thread.is_alive():
            self._lis_thread.shutdown()
            self._lis_thread.join(timeout=5.0)

        # Очистка ресурсов
        self._cleanup()

        self._is_running = False
        self._is_paused = False
        self._gui_initiated_pause = False

        self.logger.info("Система остановлена")

    def _on_pause(self):
        """Обработчик нажатия кнопки «Пауза» в GUI.

        Отправляет запрос на паузу в RobotThread, робот уходит в home
        и ожидает команды на продолжение.
        """
        if not self._is_running or self._is_paused:
            return

        self.logger.info("Приостановка системы...")

        # Запрашиваем паузу у робота
        if self._robot_thread:
            self._robot_thread.request_pause()

        self._is_paused = True
        self._gui_initiated_pause = True  # Помечаем что пауза из GUI

    def _on_resume(self):
        """Обработчик нажатия кнопки «Продолжить» в GUI.

        Отправляет сигнал продолжения в RobotThread, робот
        возобновляет работу с места остановки.
        """
        if not self._is_running:
            return

        self.logger.info("Возобновление системы...")

        # Запрашиваем продолжение у робота
        if self._robot_thread:
            self._robot_thread.request_resume()

        self._is_paused = False
        self._gui_initiated_pause = False  # Сбрасываем флаг

    def _on_rack_replaced(self):
        """Обработчик подтверждения замены штатива из GUI.

        Передаёт сигнал замены штатива в RobotThread для выхода
        из режима ожидания.
        """
        self.logger.info("Штатив заменён")

        # Подтверждаем замену штатива
        if self._robot_thread:
            self._robot_thread.confirm_rack_replaced()

    def _cleanup(self):
        """Освобождает все ресурсы: LIS клиент, робот, сканер, контекст.

        Безопасно обрабатывает ошибки при отключении каждого компонента,
        чтобы гарантировать освобождение остальных ресурсов.
        """
        # LIS клиент
        if self._lis_client:
            try:
                self._lis_client.shutdown()
            except:
                pass
            self._lis_client = None

        # Робот
        if self._robot:
            try:
                self._robot.stop_all_running_programms()
                self._robot.disconnect()
            except:
                pass
            self._robot = None

        # Сканер
        if self._scanner:
            try:
                self._scanner.disconnect()
            except:
                pass
            self._scanner = None

        self._context = None
        self._lis_thread = None
        self._robot_thread = None

    def _start_lis_stats_update(self):
        """Запускает периодическое обновление статистики ЛИС в GUI.

        Обновление происходит каждые 500 мс, пока система работает.
        Читает счётчики из PipelineContext и передаёт в GUI.
        """
        def update():
            if self._is_running and self._context:
                with self._context.counter_lock:
                    sent = self._context.sent_to_lis
                    received = self._context.received_from_lis
                ready = self._context.ready_to_sort_queue.qsize()
                in_queue = self._context.barcode_queue.qsize()

                self.gui.update_lis_stats(in_queue, received, ready)

            if self._is_running:
                self.gui.root.after(500, update)

        update()


def run_gui():
    """Точка входа для запуска GUI-приложения системы сортировки.

    Создаёт главное окно tkinter, инициализирует SorterGUI
    и GUIController, настраивает обработчик закрытия окна
    и запускает основной цикл событий.
    """
    # Логгер
    logger = create_logger("ProjectR.Robot", "robot.log")
    install_global_exception_hooks()

    logger.info("=" * 60)
    logger.info("ЗАПУСК GUI СИСТЕМЫ СОРТИРОВКИ ПРОБИРОК")
    logger.info("=" * 60)

    # Создаём главное окно
    root = tk.Tk()

    # Создаём GUI
    gui = SorterGUI(root)

    # Создаём контроллер
    controller = GUIController(gui, logger)

    # Обработчик закрытия окна
    def on_closing():
        if controller._is_running:
            controller._on_stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # Запускаем
    logger.info("GUI запущен")
    gui.run()

    logger.info("GUI завершён")


if __name__ == "__main__":
    run_gui()
