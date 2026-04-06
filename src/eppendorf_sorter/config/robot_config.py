"""Конфигурация робота, сканера и подключения к ЛИС.

Загружает из YAML-файла параметры подключения к роботу-загрузчику,
сканеру штрих-кодов и лабораторной информационной системе (ЛИС).
"""

# src/eppendorf_sorter/config/loader_config.py
from dataclasses import dataclass
from pathlib import Path
import yaml


CONFIG_PATH = Path(__file__).with_name("robot.yaml")


@dataclass(frozen=True)
class LIS:
    """Параметры подключения к лабораторной информационной системе (ЛИС).

    Attributes:
        ip: IP-адрес сервера ЛИС.
        port: Порт для подключения к серверу ЛИС.
    """

    ip: str
    port: int


@dataclass(frozen=True)
class ScannerConfig:
    """Параметры подключения к сканеру штрих-кодов.

    Attributes:
        ip: IP-адрес сканера.
        port: Порт для подключения к сканеру.
        name: Логическое имя сканера в системе.
        timeout: Таймаут ожидания ответа от сканера в секундах.
    """

    ip: str
    port: int
    name: str
    timeout: float


@dataclass(frozen=True)
class RobotcConfig:
    """Конфигурация робота-загрузчика и связанных устройств.

    Объединяет параметры подключения к роботу, сканеру
    и лабораторной информационной системе.

    Attributes:
        ip: IP-адрес робота-загрузчика.
        name: Логическое имя робота в системе.
        robot_program_name: Имя программы на контроллере робота.
        scanner: Конфигурация сканера штрих-кодов.
        lis: Конфигурация подключения к ЛИС.
    """

    ip: str
    name: str
    robot_program_name: str
    scanner: ScannerConfig
    lis: LIS


def load_robot_config(path: Path | None = None) -> RobotcConfig:
    """Загружает конфигурацию робота из YAML-файла.

    Считывает параметры робота, сканера и ЛИС из единого
    конфигурационного файла и создаёт составной объект конфигурации.

    Args:
        path: Путь к YAML-файлу конфигурации. Если не указан,
            используется файл ``robot.yaml`` рядом с текущим модулем.

    Returns:
        Экземпляр ``RobotcConfig`` с полной конфигурацией робота,
        включая вложенные конфигурации сканера и ЛИС.

    Raises:
        FileNotFoundError: Если файл конфигурации не найден.
        KeyError: Если в YAML-файле отсутствуют обязательные ключи.
    """
    cfg_path = path or CONFIG_PATH
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    robot_raw = raw["robot"]
    scanner_raw = raw["scanner"]
    lis_raw = raw["lis"]

    scanner = ScannerConfig(
        ip=scanner_raw["ip"],
        port=int(scanner_raw["port"]),
        name=scanner_raw["name"],
        timeout=float(scanner_raw["timeout"]),
    )

    lis = LIS(
        ip=lis_raw["ip"],
        port=int(lis_raw["port"])
    )

    return RobotcConfig(
        ip=robot_raw["ip"],
        name=robot_raw["name"],
        robot_program_name=robot_raw["robot_program_name"],
        scanner=scanner,
        lis=lis,
    )
