"""Реализация драйвера TCP-сканера Hikrobot.

Предоставляет класс ScannerHikrobotTCP для управления промышленным
сканером штрихкодов Hikrobot по протоколу TCP. Сканер использует
текстовый протокол: команда 'start' активирует считывание,
команда 'stop' прекращает его.
"""

import socket
import time
from functools import wraps
from typing import Callable, Tuple

from src.eppendorf_sorter.devices import Scanner, ConnectionError, DeviceError


def require_connection(func: Callable):
    """Декоратор проверки активного соединения со сканером.

    Проверяет флаг ``_connected`` и наличие сокета перед вызовом
    декорированного метода.

    Args:
        func: Декорируемый метод экземпляра сканера.

    Returns:
        Обёрнутая функция с предварительной проверкой соединения.

    Raises:
        ConnectionError: Если сканер не подключен.
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self._connected or self._socket is None:
            raise ConnectionError(f"[{self.name}] Сканер не подключен")
        return func(self, *args, **kwargs)
    return wrapper


class ScannerHikrobotTCP(Scanner):
    """Драйвер промышленного сканера Hikrobot по протоколу TCP.

    Управляет сканером через TCP-сокет с использованием текстового
    протокола команд. Для начала сканирования отправляется команда
    'start', для остановки -- 'stop'. Ответ сканера приходит
    асинхронно через тот же сокет.

    Attributes:
        name: Человекочитаемое имя сканера для логирования.
    """

    def __init__(self, name: str, ip: str, port: int):
        """Инициализация драйвера сканера Hikrobot.

        Args:
            name: Имя сканера, используемое в логах и сообщениях об ошибках.
            ip: IP-адрес сканера в сети.
            port: TCP-порт для подключения к сканеру.
        """
        self.name = name
        self._ip = ip
        self._port = port
        # Текстовые команды протокола Hikrobot для управления считыванием
        self._enable_message = "start"
        self._disable_message = "stop"
        self._socket: socket.socket | None = None
        self._connected: bool = False

    def _set_connected(self, value: bool) -> None:
        self._connected = value

    def is_connected(self) -> bool:
        """Проверить текущий статус подключения.

        Returns:
            True, если соединение со сканером установлено.
        """
        return self._connected

    def connect(self) -> None:
        """Установить TCP-соединение со сканером.

        Raises:
            ConnectionError: Если подключение не удалось (таймаут,
                отказ соединения или ошибка сокета).
        """
        print(f"[{self.name}] Подключение к сканеру {self._ip}:{self._port}")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect((self._ip, self._port))
        except socket.timeout as e:
            self._socket = None
            self._set_connected(False)
            raise ConnectionError(f"[{self.name}] Таймаут подключения к сканеру") from e
        except ConnectionRefusedError as e:
            self._socket = None
            self._set_connected(False)
            raise ConnectionError(f"[{self.name}] Подключение отклонено сканером") from e
        except OSError as e:
            self._socket = None
            self._set_connected(False)
            raise ConnectionError(f"[{self.name}] Ошибка сокета при подключении: {e}") from e
        else:
            self._socket = sock
            self._set_connected(True)
            print(f"[{self.name}] Подключение к сканеру успешно")

    def disconnect(self) -> None:
        """Закрыть TCP-соединение со сканером.

        Raises:
            DeviceError: Если при закрытии сокета произошла ошибка ОС.
        """
        print(f"[{self.name}] Отключение от сканера {self._ip}:{self._port}")
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError as e:
                # Не критично, но логически это ошибка устройства
                raise DeviceError(f"[{self.name}] Ошибка при закрытии сокета: {e}") from e
            finally:
                self._socket = None
        self._set_connected(False)

    def check_connection(self) -> bool:
        """Выполнить активную проверку соединения со сканером.

        Отправляет пустой пакет для проверки доступности сокета.
        Можно вызывать периодически в фоновом потоке.

        Returns:
            True, если соединение активно.
        """
        if self._socket is None:
            self._set_connected(False)
            return False
        try:
            self._socket.settimeout(0.5)
            self._socket.send(b"")
        except OSError:
            self._set_connected(False)
            return False
        else:
            self._set_connected(True)
            return True

    @require_connection
    def stop_scan(self) -> None:
        """Отправить сканеру команду прекращения сканирования.

        Raises:
            DeviceError: Если отправка команды 'stop' не удалась.
        """
        assert self._socket is not None
        try:
            self._socket.sendall(self._disable_message.encode("utf-8"))
        except OSError as e:
            raise DeviceError(f"[{self.name}] Ошибка при отправке stop: {e}") from e

    @require_connection
    def scan(self, timeout: float) -> tuple[str, float]:
        """Выполнить сканирование с ожиданием результата.

        Отправляет команду 'start' для активации считывания, затем
        ожидает ответ от сканера в цикле до истечения таймаута.
        При успешном считывании отправляет 'stop' для прекращения
        работы лазера/подсветки сканера.

        Args:
            timeout: Максимальное время ожидания считывания в секундах.

        Returns:
            Кортеж (результат, время_recv), где результат -- считанная
            строка кода, а время_recv -- время выполнения recv() в секундах.
            Если код не считан за timeout, возвращает ('NoRead', 0.0).

        Raises:
            DeviceError: Если произошла ошибка связи при старте
                сканирования или при чтении данных из сокета.
        """
        assert self._socket is not None
        sock = self._socket

        # Отправляем 'start' -- сканер начинает считывание
        try:
            sock.settimeout(0.1)
            sock.sendall(self._enable_message.encode("utf-8"))
        except OSError as e:
            raise DeviceError(f"[{self.name}] Ошибка при старте сканирования: {e}") from e

        start_time = time.perf_counter()

        while time.perf_counter() - start_time < timeout:
            try:
                recv_start = time.perf_counter()
                data = sock.recv(1024)
                recv_time = time.perf_counter() - recv_start

                if not data:
                    continue

                result = data.decode("utf-8").replace("\r", "").strip()

                if result and result != "NoRead":
                    # Отправляем 'stop' -- сканер прекращает считывание
                    try:
                        sock.sendall(self._disable_message.encode("utf-8"))
                    except OSError:
                        pass

                    return result, recv_time

            except socket.timeout:
                continue
            except OSError as e:
                raise DeviceError(f"[{self.name}] Ошибка при чтении: {e}") from e

        # Таймаут истёк, код не считан -- останавливаем сканер
        try:
            sock.sendall(self._disable_message.encode("utf-8"))
        except OSError:
            pass

        return "NoRead", 0.0

if __name__ == '__main__':
    scanner = ScannerHikrobotTCP(name="Scanner", ip='192.168.124.4', port= 6000)
    print(scanner.connect())
    #scanner.disconnect()
    arr = []

    elem = scanner.scan(timeout=1)
    print(elem)
    scanner.stop_scan()
