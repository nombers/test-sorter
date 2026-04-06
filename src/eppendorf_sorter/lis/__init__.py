"""Пакет для взаимодействия с лабораторной информационной системой (ЛИС).

Предоставляет HTTP-клиент для получения информации о пробирках
и парсинг типов тестов из ответов ЛИС.
"""

from .client import get_tube_info_sync, parse_test_type, LISClient

__all__ = [
    "get_tube_info_sync",
    "parse_test_type",
    "LISClient",
]
