# devices/scanners/scanner_hikrobot_tcp.py
import socket
import time
from functools import wraps
from typing import Callable, Tuple

from src.eppendorf_sorter.devices import Scanner, ConnectionError, DeviceError


def require_connection(func: Callable):
    """Декоратор для проверки соединения со сканером."""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self._connected or self._socket is None:
            raise ConnectionError(f"[{self.name}] Сканер не подключен")
        return func(self, *args, **kwargs)
    return wrapper


class ScannerHikrobotTCP(Scanner):
    """
    TCP-сканер Hikrobot.
    Использует текстовые команды 'start' / 'stop' для управления сканированием.
    """

    def __init__(self, name: str, ip: str, port: int):
        self.name = name
        self._ip = ip
        self._port = port
        self._enable_message = "start"
        self._disable_message = "stop"
        self._socket: socket.socket | None = None
        self._connected: bool = False

    def _set_connected(self, value: bool) -> None:
        self._connected = value

    def is_connected(self) -> bool:
        """Возвращает текущий статус подключения."""
        return self._connected

    def connect(self) -> None:
        """Подключение к сканеру по TCP."""
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
        """Отключение от сканера."""
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
        """
        Опциональная активная проверка соединения.
        Можно вызывать периодически в фоне.
        """
        if self._socket is None:
            self._set_connected(False)
            return False
        try:
            self._socket.settimeout(0.5)
            # Небольшой "ping": отправляем пустой байт
            self._socket.send(b"")
        except OSError:
            self._set_connected(False)
            return False
        else:
            self._set_connected(True)
            return True

    @require_connection
    def stop_scan(self) -> None:
        """Посылает команде сканеру прекратить сканирование."""
        assert self._socket is not None 
        try:
            self._socket.sendall(self._disable_message.encode("utf-8"))
        except OSError as e:
            raise DeviceError(f"[{self.name}] Ошибка при отправке stop: {e}") from e

    @require_connection
    def scan(self, timeout: float) -> tuple[str, float]:
        """
        Сканирование: возвращает (результат, время_операции_recv).
        Если ничего не считано, возвращает ("NoRead", 0.0).
        """
        assert self._socket is not None
        sock = self._socket

        # Запустить процесс сканирования
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
                    # Останавливаем устройство
                    try:
                        sock.sendall(self._disable_message.encode("utf-8"))
                    except OSError:
                        pass

                    return result, recv_time

            except socket.timeout:
                continue
            except OSError as e:
                raise DeviceError(f"[{self.name}] Ошибка при чтении: {e}") from e

        # Если не считали код
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
