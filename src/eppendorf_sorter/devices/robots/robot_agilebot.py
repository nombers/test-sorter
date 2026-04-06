"""Реализация драйвера робота Agilebot.

Предоставляет класс RobotAgilebot для управления коллаборативным
роботом Agilebot через его SDK, включая запуск программ, работу
с регистрами и цифровым вводом-выводом.
"""

from Agilebot.IR.A.arm import Arm
from Agilebot.IR.A.status_code import StatusCodeEnum
from Agilebot.IR.A.sdk_types import SignalType, SignalValue
from typing import List, Callable
from functools import wraps


from src.eppendorf_sorter.devices import ConnectionError, DeviceError, CellRobot


def require_connection(func: Callable):
    """Декоратор проверки активного соединения с роботом.

    Проверяет флаг ``_connection`` экземпляра перед вызовом
    декорированного метода. Если соединение не установлено,
    выбрасывает исключение.

    Args:
        func: Декорируемый метод экземпляра робота.

    Returns:
        Обёрнутая функция с предварительной проверкой соединения.

    Raises:
        ConnectionError: Если робот не подключен.
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self._connection:
            raise ConnectionError(f"Робот {self.name} не подключен")
        return func(self, *args, **kwargs)
    return wrapper


class RobotProgrammStateDecoder:
    """Декодер числовых статусов программ робота Agilebot.

    Преобразует целочисленные коды состояний программ,
    возвращаемые SDK, в читаемые строковые представления.
    """

    @staticmethod
    def decode_programm_state(programm_state: int):
        """Преобразовать числовой код состояния программы в строку.

        Args:
            programm_state: Числовой код состояния из SDK
                (0 — IDLE, 1 — RUNNING, 2 — PAUSED).

        Returns:
            Строковое представление состояния ('IDLE', 'RUNNING',
            'PAUSED') или None, если код неизвестен.
        """
        programm_states = {
            None: "IDLE",
            0: "IDLE",
            1: "RUNNING",
            2: "PAUSED"
        }
        return programm_states.get(programm_state)


class RobotAgilebot(CellRobot):
    """Драйвер коллаборативного робота Agilebot на основе его SDK.

    Реализует полный интерфейс CellRobot: управление программами,
    чтение/запись регистров и цифровых сигналов. Перед использованием
    основных функций необходимо выполнить подключение методом connect().

    Attributes:
        name: Человекочитаемое имя робота для логирования.
        ip: IP-адрес контроллера робота.
        arm: Экземпляр SDK Agilebot Arm для низкоуровневого взаимодействия.
    """

    def __init__(self, name: str, ip: str):
        """Инициализация драйвера робота Agilebot.

        Args:
            name: Имя робота, используемое в логах и сообщениях об ошибках.
            ip: IP-адрес контроллера робота для подключения.
        """
        self.name = name
        self.ip = ip
        self._connection = False
        self.arm = Arm()

    def _check_status(self, ret, msg: str = ""):
        """Проверить код возврата SDK и выбросить исключение при ошибке.

        Args:
            ret: Код возврата от метода SDK (StatusCodeEnum).
            msg: Дополнительное сообщение для контекста ошибки.

        Raises:
            DeviceError: Если код возврата отличается от StatusCodeEnum.OK.
        """
        if ret != StatusCodeEnum.OK:
            raise DeviceError(
                f"[{self.name}] Ошибка SDK Agilebot: {msg or ret}"
            )

    def connect(self) -> None:
        """Установить соединение с роботом по IP-адресу.

        Raises:
            ConnectionError: Если подключение к роботу не удалось.
        """
        print(f"[{self.name}] Подключение к роботу {self.ip}")
        ret = self.arm.connect(self.ip)
        try:
            self._check_status(ret, "connect")
        except DeviceError as e:
            self._connection = False
            raise ConnectionError(f"[{self.name}] Не удалось подключиться к {self.ip}") from e
        else:
            self._connection = True

    @require_connection
    def disconnect(self) -> None:
        """Отключиться от робота."""
        print(f"[{self.name}] Отключение от робота")
        self._connection = False

    def is_connected(self) -> bool:
        """Проверить текущий статус подключения.

        Returns:
            True, если соединение с роботом установлено.
        """
        return self._connection

    @require_connection
    def power_on_servo(self) -> None:
        """Включить сервоприводы робота.

        Raises:
            DeviceError: Если включение приводов не удалось.
        """
        ret = self.arm.execution.servo_on()
        self._check_status(ret)

    @require_connection
    def power_off_servo(self) -> None:
        """Выключить сервоприводы робота.

        Raises:
            DeviceError: Если выключение приводов не удалось.
        """
        ret = self.arm.execution.servo_off()
        self._check_status(ret)

    @require_connection
    def start_program(self, program_name: str) -> None:
        """Запустить программу на роботе.

        Args:
            program_name: Имя программы для запуска.

        Raises:
            DeviceError: Если запуск программы не удался.
        """
        ret = self.arm.execution.start(program_name)
        self._check_status(ret)

    @require_connection
    def pause_program(self) -> None:
        """Поставить на паузу все запущенные программы.

        Raises:
            DeviceError: Если получение списка программ или пауза не удались.
        """
        programs_list, ret = self.arm.execution.all_running_programs()
        self._check_status(ret)
        for program in programs_list:
            ret = self.arm.execution.pause(program.program_name)
            self._check_status(ret)

    @require_connection
    def resume_program(self) -> None:
        """Возобновить выполнение всех приостановленных программ.

        Raises:
            DeviceError: Если получение списка программ или возобновление не удались.
        """
        programs_list, ret = self.arm.execution.all_running_programs()
        self._check_status(ret)
        for program in programs_list:
            ret = self.arm.execution.resume(program.program_name)
            self._check_status(ret)

    @require_connection
    def stop_program(self, program_name) -> None:
        """Остановить и завершить указанную программу.

        Args:
            program_name: Имя программы для остановки.

        Raises:
            DeviceError: Если остановка программы не удалась.
        """
        ret = self.arm.execution.stop(program_name)
        self._check_status(ret)

    @require_connection
    def reset_errors(self) -> None:
        """Сбросить все активные ошибки и тревоги робота.

        Raises:
            DeviceError: Если сброс ошибок не удался.
        """
        ret = self.arm.alarm.reset()
        self._check_status(ret)

    @require_connection
    def get_all_active_alarms(self) -> List:
        """Получить список уникальных имён всех активных тревог.

        Returns:
            Список уникальных строковых имён активных тревог.
            Пустой список, если тревог нет.

        Raises:
            DeviceError: Если запрос тревог не удался.
        """
        alarms, ret = self.arm.alarm.get_all_active_alarms()
        self._check_status(ret)
        alarms_list = [alarm for alarm in alarms]
        if len(alarms_list) > 0:
            unique_names = list(set(alarm.Name for alarm in alarms_list))
            return unique_names
        else:
            return []

    @require_connection
    def get_all_running_programms_states(self) -> str:
        """Получить обобщённый статус выполняемых программ.

        Returns:
            Одна из строк: 'IDLE' (нет программ), 'RUNNING', 'PAUSED',
            или 'MIXED' (несколько программ в разных состояниях).

        Raises:
            DeviceError: Если запрос списка программ не удался.
        """
        programs_list, ret = self.arm.execution.all_running_programs()
        self._check_status(ret)
        program_states = [RobotProgrammStateDecoder.decode_programm_state(program.program_status) for program in programs_list]
        if len(program_states) == 1:
            return program_states[0]
        elif len(program_states) == 0:
            return "IDLE"
        else:
            return "MIXED"

    @require_connection
    def stop_all_running_programms(self) -> None:
        """Остановить все выполняемые программы робота.

        Raises:
            DeviceError: Если получение списка или остановка программы не удались.
        """
        programs_list, ret = self.arm.execution.all_running_programs()
        self._check_status(ret)
        for program in programs_list:
            print(program.program_name)
            self.stop_program(program.program_name)

    @require_connection
    def get_string_register(self, register_id: int) -> str:
        """Прочитать значение строкового регистра (SR).

        Args:
            register_id: Идентификатор строкового регистра.

        Returns:
            Строковое значение регистра.

        Raises:
            DeviceError: Если чтение регистра не удалось.
        """
        value, ret = self.arm.register.read_SR(register_id)
        self._check_status(ret)
        return value

    @require_connection
    def set_string_register(self, register_id: int, string: str) -> None:
        """Записать значение в строковый регистр (SR).

        Args:
            register_id: Идентификатор строкового регистра.
            string: Строка для записи в регистр.

        Raises:
            DeviceError: Если запись в регистр не удалась.
        """
        ret = self.arm.register.write_SR(register_id, string)
        self._check_status(ret)

    @require_connection
    def get_number_register(self, register_id: int) -> int|float:
        """Прочитать значение числового регистра (NR).

        Args:
            register_id: Идентификатор числового регистра.

        Returns:
            Числовое значение регистра.

        Raises:
            DeviceError: Если чтение регистра не удалось.
        """
        value, ret = self.arm.register.read_R(register_id)
        self._check_status(ret)
        return value

    @require_connection
    def set_number_register(self, register_id: int, value: int|float) -> None:
        """Записать значение в числовой регистр (NR).

        Args:
            register_id: Идентификатор числового регистра.
            value: Число для записи в регистр.

        Raises:
            DeviceError: Если запись в регистр не удалась.
        """
        ret = self.arm.register.write_R(register_id, value)
        self._check_status(ret)

    @require_connection
    def get_DO(self, do_id: int) -> bool:
        """Прочитать значение цифрового выхода (DO).

        Args:
            do_id: Идентификатор цифрового выхода.

        Returns:
            Логическое состояние выхода.

        Raises:
            DeviceError: Если чтение не удалось.
        """
        value, ret = self.arm.digital_signals.read(SignalType.DO, int(do_id))
        self._check_status(ret)
        return bool(value)

    @require_connection
    def set_DO(self, do_id: int, value: bool) -> None:
        """Установить значение цифрового выхода (DO).

        Args:
            do_id: Идентификатор цифрового выхода.
            value: True для включения (ON), False для выключения (OFF).

        Raises:
            DeviceError: Если запись не удалась.
        """
        if value:
            ret = self.arm.digital_signals.write(SignalType.DO, do_id, SignalValue.ON)
            self._check_status(ret)
        else:
            ret = self.arm.digital_signals.write(SignalType.DO, do_id, SignalValue.OFF)
            self._check_status(ret)

    @require_connection
    def get_DI(self, di_id) -> bool:
        """Прочитать значение цифрового входа (DI).

        Args:
            di_id: Идентификатор цифрового входа.

        Returns:
            Логическое состояние входа.

        Raises:
            DeviceError: Если чтение не удалось.
        """
        value, ret = self.arm.digital_signals.read(SignalType.DI, int(di_id))
        self._check_status(ret)
        return bool(value)

    def __str__(self):
        return f"Робот {self.name}, ip = {self.ip}, статус подключения -> {self._connection}"


if __name__ == "__main__":
    def main():
        import time
        loading_robot = RobotAgilebot(name = "Robot_Loading", ip = "192.168.124.2")
        loading_robot.connect()
        # unloading_robot = RobotManipulator(name = "Robot_Loading", ip = "192.168.124.2")
        # unloading_robot.connect()
        # loading_robot.stop_program("Loading_Robot")
        # print(loading_robot.get_all_running_programms_states())
        from time import perf_counter
        # loading_robot.start_program("Loading_Robot")
        # print(loading_robot.get_all_running_programms_states())
        # loading_robot.pause_program()
        # print(loading_robot.get_all_running_programms_states())
        # loading_robot.stop_program("Loading_Robot")
        # print(loading_robot.get_all_running_programms_states())
        # while True:
        #     # print(loading_robot.get_all_running_programms_states())
        #     print(loading_robot.get_all_active_alarms())
        #     time.sleep(1)

        st = perf_counter()
        e = loading_robot.get_string_register(1)
        print(f"{perf_counter()-st} --- GET SR")

        st = perf_counter()
        e = loading_robot.get_number_register(1)
        print(f"{perf_counter()-st} --- GET NR")


        e = loading_robot.set_string_register(1, "23")
        print(f"{perf_counter()-st} --- SET SR")

        st = perf_counter()
        e = loading_robot.set_number_register(1, 2)
        print(f"{perf_counter()-st} --- SET NR")

        st = perf_counter()
        e = loading_robot.set_DO(1, True)
        print(f"{perf_counter()-st} --- SET DO")

        st = perf_counter()
        e = loading_robot.get_DO(1)
        print(f"{perf_counter()-st} --- GET DO")

        st = perf_counter()
        e = loading_robot.get_DI(1)
        print(f"{perf_counter()-st} --- GET DI")

        st = perf_counter()
        e = int("3455")

        # loading_robot.power_off_servo()

        print(loading_robot.get_all_active_alarms())
        loading_robot.reset_errors()
        print(loading_robot.get_all_active_alarms())
        print(loading_robot.get_all_running_programms_states())
        loading_robot.pause_program()
    main()
