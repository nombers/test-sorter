# src/eppendorf_sorter/orchestration/operator_input.py
"""
Модуль для обработки команд оператора из терминала.
Позволяет оператору подтверждать замену штативов и управлять системой.
"""
import threading
import logging
from typing import Optional, Callable, Dict


class OperatorInputHandler:
    """
    Обработчик команд оператора из stdin.
    Работает в отдельном потоке, не блокируя основную логику.

    Поддерживаемые команды:
        start   - подтверждение замены штативов / продолжить после паузы
        pause   - поставить на паузу (после текущей атомарной операции)
        stop    - остановка системы
        status  - показать текущий статус
        help    - показать справку
    """

    def __init__(self, logger: logging.Logger, stop_event: threading.Event):
        """
        Args:
            logger: Логгер для вывода сообщений
            stop_event: Глобальное событие остановки системы
        """
        self.logger = logger
        self.stop_event = stop_event

        # События для сигнализации о командах
        self.rack_replaced_event = threading.Event()

        # Флаг и событие для паузы
        self._pause_requested = False  # Запрос на паузу (ставится командой pause)
        self._paused = False           # Система на паузе
        self._resume_event = threading.Event()  # Событие для выхода из паузы

        # Callback для получения статуса (устанавливается извне)
        self._status_callback: Optional[Callable[[], str]] = None

        # Поток чтения stdin
        self._input_thread: Optional[threading.Thread] = None
        self._running = False
    
    def set_status_callback(self, callback: Callable[[], str]):
        """Установить callback для получения статуса системы."""
        self._status_callback = callback

    def is_paused(self) -> bool:
        """Проверить, находится ли система на паузе."""
        return self._paused

    def is_pause_requested(self) -> bool:
        """Проверить, запрошена ли пауза."""
        return self._pause_requested

    def check_pause(self) -> bool:
        """
        Проверить и обработать запрос на паузу.

        Вызывается после каждой атомарной операции.
        Если запрошена пауза - блокирует выполнение до команды resume/start.

        Returns:
            True если можно продолжать, False если stop_event
        """
        if self.stop_event.is_set():
            return False

        if not self._pause_requested:
            return True

        # Входим в режим паузы
        self._paused = True
        self._pause_requested = False
        self._resume_event.clear()

        self.logger.info("\n" + "=" * 60)
        self.logger.info("⏸  СИСТЕМА НА ПАУЗЕ")
        self.logger.info("Прогресс сохранён. Введите 'start' для продолжения")
        self.logger.info("=" * 60 + "\n")

        # Ждём команды на продолжение или stop
        while not self.stop_event.is_set():
            if self._resume_event.wait(timeout=1.0):
                self._paused = False
                self.logger.info("▶  Продолжение работы...")
                return True

        self._paused = False
        return False

    def start(self):
        """Запустить обработчик команд."""
        if self._running:
            return
        
        self._running = True
        self._input_thread = threading.Thread(
            target=self._input_loop,
            name="OperatorInputThread",
            daemon=True
        )
        self._input_thread.start()
        self.logger.info("Обработчик команд оператора запущен")
        self._print_help()
    
    def stop(self):
        """Остановить обработчик команд."""
        self._running = False
        if self._input_thread and self._input_thread.is_alive():
            # Поток daemon, завершится сам
            pass
        self.logger.info("Обработчик команд оператора остановлен")
    
    def wait_for_rack_replacement(self, timeout: Optional[float] = None) -> bool:
        """
        Ожидать подтверждения замены штативов от оператора.
        
        Args:
            timeout: Максимальное время ожидания (None = бесконечно)
            
        Returns:
            True если получено подтверждение, False если timeout или stop_event
        """
        self.rack_replaced_event.clear()
        
        self.logger.info("\n" + "="*60)
        self.logger.info("⏳ ОЖИДАНИЕ ЗАМЕНЫ ШТАТИВОВ")
        self.logger.info("Введите 'start' после замены штативов")
        self.logger.info("="*60 + "\n")
        
        # Ждём либо подтверждения, либо stop_event
        while not self.stop_event.is_set():
            if self.rack_replaced_event.wait(timeout=1.0):
                self.logger.info("✓ Получено подтверждение замены штативов")
                return True
            
            if timeout is not None:
                timeout -= 1.0
                if timeout <= 0:
                    self.logger.warning("Таймаут ожидания замены штативов")
                    return False
        
        return False
    
    def _input_loop(self):
        """Основной цикл чтения команд из stdin."""
        while self._running and not self.stop_event.is_set():
            try:
                # input() блокирующий, но поток daemon - завершится с программой
                user_input = input().strip().lower()
                
                if not user_input:
                    continue
                
                self._process_command(user_input)
                
            except EOFError:
                # stdin закрыт
                self.logger.debug("stdin закрыт")
                break
            except Exception as e:
                self.logger.error(f"Ошибка чтения stdin: {e}")
    
    def _process_command(self, command: str):
        """Обработать команду от оператора."""

        if command in ("start", "s", "go", "готово", "старт", "resume", "r"):
            self._handle_start()

        elif command in ("pause", "p", "пауза"):
            self._handle_pause()

        elif command in ("stop", "quit", "exit", "стоп", "выход"):
            self._handle_stop()

        elif command in ("status", "stat", "статус"):
            self._handle_status()

        elif command in ("help", "h", "?", "помощь"):
            self._print_help()

        else:
            print(f"❌ Неизвестная команда: '{command}'. Введите 'help' для справки.")

    def _handle_start(self):
        """Обработка команды start - продолжить работу / выйти из паузы."""
        if self._paused:
            print("▶  Выход из паузы...")
            self._resume_event.set()
        else:
            print("✓ Принято: продолжение работы")
            self.rack_replaced_event.set()

    def _handle_pause(self):
        """Обработка команды pause - запрос на паузу."""
        if self._paused:
            print("ℹ  Система уже на паузе")
        elif self._pause_requested:
            print("ℹ  Пауза уже запрошена, ожидание завершения текущей операции...")
        else:
            self._pause_requested = True
            print("⏸  Пауза запрошена. Система остановится после текущей атомарной операции.")

    def _handle_stop(self):
        """Обработка команды stop - остановка системы."""
        print("⚠ Остановка системы...")
        self.stop_event.set()
        # Также снимаем паузу, чтобы поток мог завершиться
        self._resume_event.set()

    def _handle_status(self):
        """Обработка команды status - вывод статуса системы."""
        # Показываем состояние паузы
        if self._paused:
            print("⏸  Статус: НА ПАУЗЕ")
        elif self._pause_requested:
            print("⏳ Статус: Пауза запрошена...")
        else:
            print("▶  Статус: Работает")

        if self._status_callback:
            status = self._status_callback()
            print(f"\n📊 СТАТУС СИСТЕМЫ:\n{status}\n")
        else:
            print("Детальный статус недоступен")

    def _print_help(self):
        """Вывести справку по командам."""
        help_text = """
╔══════════════════════════════════════════════════════════════╗
║                    КОМАНДЫ ОПЕРАТОРА                         ║
╠══════════════════════════════════════════════════════════════╣
║  start, s, go    - Продолжить работу / выйти из паузы        ║
║  pause, p        - Поставить на паузу (после текущей операции)║
║  stop, exit      - Остановить систему                        ║
║  status, stat    - Показать текущий статус                   ║
║  help, h, ?      - Показать эту справку                      ║
╚══════════════════════════════════════════════════════════════╝
"""
        print(help_text)
