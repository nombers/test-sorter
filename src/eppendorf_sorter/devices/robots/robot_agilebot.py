# devices/robots/robot_agilebot.py
from Agilebot.IR.A.arm import Arm
from Agilebot.IR.A.status_code import StatusCodeEnum
from Agilebot.IR.A.sdk_types import SignalType, SignalValue
from typing import List, Callable
from functools import wraps


from src.eppendorf_sorter.devices import ConnectionError, DeviceError, CellRobot


def require_connection(func: Callable):
    """Декоратор для проверки соединения"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self._connection:
            raise ConnectionError(f"Робот {self.name} не подключен")
        return func(self, *args, **kwargs)
    return wrapper


class RobotProgrammStateDecoder:
    """Проверка кода возврата SDK и выброс DeviceError при ошибке."""
    @staticmethod 
    def decode_programm_state(programm_state: int):
        programm_states = {
            None: "IDLE",
            0: "IDLE",
            1: "RUNNING",
            2: "PAUSED"
        }
        return programm_states.get(programm_state)
    

class RobotAgilebot(CellRobot):
    """
    Класс, основанный на SDK Agilebot - содержит методы, которые позволяют 
    использовать оснонвые функции коллаборативного робота
    - Обязательно овыполнить подключение connect() после создания экземпляра для успешного использования
    """
    def __init__(self, name: str, ip: str):
        self.name = name
        self.ip = ip
        self._connection = False
        self.arm = Arm()

    def _check_status(self, ret, msg: str = ""):
        "Безопасная замена assert ret для надженого дебага"
        if ret != StatusCodeEnum.OK:
            raise DeviceError(
                f"[{self.name}] Ошибка SDK Agilebot: {msg or ret}"
            )

    def connect(self) -> None:
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
        """Отключение от робота"""
        print(f"[{self.name}] Отключение от робота")
        self._connection = False

    def is_connected(self) -> bool:
        return self._connection
    
    @require_connection
    def power_on_servo(self) -> None:
        "Включить привода робота"
        ret = self.arm.execution.servo_on()
        self._check_status(ret)

    @require_connection
    def power_off_servo(self) -> None:
        "Выключить привода робота"
        ret = self.arm.execution.servo_off()
        self._check_status(ret)

    @require_connection
    def start_program(self, program_name: str) -> None:
        """Запуск программы с выбранным именем"""
        ret = self.arm.execution.start(program_name)
        self._check_status(ret)

    @require_connection
    def pause_program(self) -> None:
        """Постановка программы на паузу"""
        programs_list, ret = self.arm.execution.all_running_programs()
        self._check_status(ret)
        for program in programs_list:
            ret = self.arm.execution.pause(program.program_name)
            self._check_status(ret)

    @require_connection
    def resume_program(self) -> None:
        """Снятие программы с паузы"""
        programs_list, ret = self.arm.execution.all_running_programs()
        self._check_status(ret)
        for program in programs_list:
            ret = self.arm.execution.resume(program.program_name)
            self._check_status(ret)

    @require_connection
    def stop_program(self, program_name) -> None:
        """Остановка и завершение программы"""
        ret = self.arm.execution.stop(program_name)
        self._check_status(ret)

    @require_connection
    def reset_errors(self) -> None:
        """Сбросить все ошибки и ресетнуть робота"""
        ret = self.arm.alarm.reset()
        self._check_status(ret)

    @require_connection
    def get_all_active_alarms(self) -> List:
        """Получить все активные тревоги"""
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
        """Возвращает один из 3 возможных статусов робота"""
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
        "Завершает ВСЕ активные программы робота"
        programs_list, ret = self.arm.execution.all_running_programs()
        self._check_status(ret)
        for program in programs_list:
            print(program.program_name)
            self.stop_program(program.program_name)

    @require_connection
    def get_string_register(self, register_id: int) -> str:
        """Возвращает SR по заданному ID"""
        value, ret = self.arm.register.read_SR(register_id)
        self._check_status(ret)
        return value

    @require_connection
    def set_string_register(self, register_id: int, string: str) -> None:
        """Установка SR с указанным ID"""
        ret = self.arm.register.write_SR(register_id, string)
        self._check_status(ret)

    @require_connection
    def get_number_register(self, register_id: int) -> int|float:
        """Возвращает NR по заданному ID"""
        value, ret = self.arm.register.read_R(register_id)
        self._check_status(ret)
        return value

    @require_connection
    def set_number_register(self, register_id: int, value: int|float) -> None:
        """Установка NR с указанным ID"""
        ret = self.arm.register.write_R(register_id, value)
        self._check_status(ret)

    @require_connection
    def get_DO(self, do_id: int) -> bool:
        """Возвращает булевое значение DO по заданному ID"""
        value, ret = self.arm.digital_signals.read(SignalType.DO, int(do_id))
        self._check_status(ret)
        return bool(value)
    
    @require_connection
    def set_DO(self, do_id: int, value: bool) -> None:
        """Устанавливает булевое значение DO по заданному ID"""
        if value: 
            ret = self.arm.digital_signals.write(SignalType.DO, do_id, SignalValue.ON)
            self._check_status(ret)
        else:
            ret = self.arm.digital_signals.write(SignalType.DO, do_id, SignalValue.OFF)
            self._check_status(ret)

    @require_connection
    def get_DI(self, di_id) -> bool:
        """Возвращает булевое значение DI по заданному ID"""
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

