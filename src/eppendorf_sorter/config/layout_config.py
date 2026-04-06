"""Конфигурация компоновки системы (расположение штативов).

Загружает из YAML-файла параметры физического расположения штативов
в рабочей зоне робота.
"""

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class SystemLayoutConfig:
    """Конфигурация расположения штативов в системе.

    Определяет номера позиций штативов для загрузки и выгрузки
    пробирок в рабочей зоне робота.

    Attributes:
        unloading_rack: Номер позиции штатива для выгрузки пробирок.
        loading_rack: Номер позиции штатива для загрузки пробирок.
    """

    unloading_rack: int
    loading_rack: int


def load_system_layout_config(path: Path | None = None) -> SystemLayoutConfig:
    """Загружает конфигурацию компоновки системы из YAML-файла.

    Args:
        path: Путь к YAML-файлу конфигурации. Если не указан,
            используется файл ``layout.yaml`` рядом с текущим модулем.

    Returns:
        Экземпляр ``SystemLayoutConfig`` с параметрами расположения штативов.

    Raises:
        FileNotFoundError: Если файл конфигурации не найден.
        KeyError: Если в YAML-файле отсутствуют обязательные ключи.
    """
    if path is None:
        from pathlib import Path
        path = Path(__file__).with_name("layout.yaml")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return SystemLayoutConfig(
        unloading_rack=raw["rack"]["unloading"],
        loading_rack=raw["rack"]["loading"],
    )
