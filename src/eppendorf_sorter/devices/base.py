"""Базовые абстрактные классы и исключения для устройств.

Определяет иерархию абстрактных интерфейсов (Robot, RobotIO,
RobotRegisters, Scanner) и составной класс CellRobot, а также
общие исключения DeviceError и ConnectionError.
"""

from abc import ABC, abstractmethod


class DeviceError(Exception):
    """Базовое исключение для ошибок устройств.

    Используется как родительский класс для всех исключений,
    связанных с аппаратными устройствами системы.
    """


class ConnectionError(DeviceError):
    """Ошибка подключения к устройству.

    Выбрасывается при неудачной попытке установить или
    использовать соединение с устройством.
    """


class Robot(ABC):
    """Абстрактный интерфейс робота-манипулятора.

    Определяет базовый контракт для управления жизненным циклом
    подключения и запуском/остановкой программ робота.
    """

    @abstractmethod
    def connect(self) -> None:
        """Установить соединение с роботом.

        Raises:
            ConnectionError: Если подключение не удалось.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Разорвать соединение с роботом.

        Raises:
            DeviceError: Если при отключении произошла ошибка.
        """
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Проверить текущий статус подключения.

        Returns:
            True, если соединение с роботом установлено.
        """
        ...

    @abstractmethod
    def start_program(self, program_name: str) -> None:
        """Запустить программу на роботе.

        Args:
            program_name: Имя программы для запуска.

        Raises:
            DeviceError: Если запуск программы не удался.
        """
        ...

    @abstractmethod
    def stop_program(self, program_name: str) -> None:
        """Остановить выполняемую программу.

        Args:
            program_name: Имя программы для остановки.

        Raises:
            DeviceError: Если остановка программы не удалась.
        """
        ...

    @abstractmethod
    def stop_all_running_programms(self) -> None:
        """Остановить все выполняемые программы.

        Raises:
            DeviceError: Если остановка одной из программ не удалась.
        """
        ...

    @abstractmethod
    def reset_errors(self) -> None:
        """Сбросить все активные ошибки и тревоги робота.

        Raises:
            DeviceError: Если сброс ошибок не удался.
        """
        ...


class RobotIO(ABC):
    """Абстрактный интерфейс цифровых входов/выходов робота.

    Предоставляет методы для чтения цифровых входов (DI)
    и чтения/записи цифровых выходов (DO).
    """

    @abstractmethod
    def get_DI(self, di_id: int) -> bool:
        """Прочитать значение цифрового входа.

        Args:
            di_id: Идентификатор цифрового входа.

        Returns:
            Текущее логическое состояние входа.

        Raises:
            DeviceError: Если чтение не удалось.
        """
        ...

    @abstractmethod
    def get_DO(self, do_id: int) -> bool:
        """Прочитать текущее значение цифрового выхода.

        Args:
            do_id: Идентификатор цифрового выхода.

        Returns:
            Текущее логическое состояние выхода.

        Raises:
            DeviceError: Если чтение не удалось.
        """
        ...

    @abstractmethod
    def set_DO(self, do_id: int, value: bool) -> None:
        """Установить значение цифрового выхода.

        Args:
            do_id: Идентификатор цифрового выхода.
            value: Значение для установки (True — включить, False — выключить).

        Raises:
            DeviceError: Если запись не удалась.
        """
        ...


class RobotRegisters(ABC):
    """Абстрактный интерфейс доступа к регистрам робота.

    Предоставляет методы для чтения и записи строковых (SR)
    и числовых (NR) регистров робота.
    """

    @abstractmethod
    def get_string_register(self, register_id: int) -> str:
        """Прочитать значение строкового регистра.

        Args:
            register_id: Идентификатор строкового регистра.

        Returns:
            Строковое значение регистра.

        Raises:
            DeviceError: Если чтение не удалось.
        """
        ...

    @abstractmethod
    def set_string_register(self, register_id: int, value: str) -> None:
        """Записать значение в строковый регистр.

        Args:
            register_id: Идентификатор строкового регистра.
            value: Строка для записи.

        Raises:
            DeviceError: Если запись не удалась.
        """
        ...

    @abstractmethod
    def get_number_register(self, register_id: int) -> int | float:
        """Прочитать значение числового регистра.

        Args:
            register_id: Идентификатор числового регистра.

        Returns:
            Числовое значение регистра (целое или с плавающей точкой).

        Raises:
            DeviceError: Если чтение не удалось.
        """
        ...

    @abstractmethod
    def set_number_register(self, register_id: int, value: int | float) -> None:
        """Записать значение в числовой регистр.

        Args:
            register_id: Идентификатор числового регистра.
            value: Число для записи.

        Raises:
            DeviceError: Если запись не удалась.
        """
        ...


class CellRobot(Robot, RobotIO, RobotRegisters, ABC):
    """Составной интерфейс робота для автоматизированной ячейки.

    Объединяет возможности управления программами (Robot),
    цифрового ввода-вывода (RobotIO) и работы с регистрами
    (RobotRegisters) в единый контракт, необходимый для
    работы в автоматизированной ячейке сортировки.
    """
    pass


class Scanner(ABC):
    """Абстрактный интерфейс сканера штрихкодов / QR-кодов.

    Определяет контракт для подключения к сканеру и выполнения
    операций считывания кодов с заданным таймаутом.
    """

    @abstractmethod
    def connect(self) -> None:
        """Установить соединение со сканером.

        Raises:
            ConnectionError: Если подключение не удалось.
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Разорвать соединение со сканером.

        Raises:
            DeviceError: Если при отключении произошла ошибка.
        """
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Проверить текущий статус подключения.

        Returns:
            True, если соединение со сканером установлено.
        """
        ...

    @abstractmethod
    def scan(self, timeout: float) -> tuple[str, float]:
        """Выполнить сканирование с ожиданием результата.

        Args:
            timeout: Максимальное время ожидания считывания в секундах.

        Returns:
            Кортеж (строка_кода, время_приёма_сек).
            Если код не считан за timeout, возвращает ('NoRead', 0.0).

        Raises:
            DeviceError: Если при сканировании произошла ошибка связи.
        """
        ...
