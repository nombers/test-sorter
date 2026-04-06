"""
Ручной тест HTTP-клиента к ЛИС серверу.

Используется для быстрой проверки связи с сервером ЛИС
и корректности ответов без запуска основного приложения.

Использование:
    python test.py
"""

import requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_tube_info(barcode: str, host: str = "192.168.12.80", port: int = 7117) -> dict | None:
    """Запрашивает информацию о пробирке у ЛИС сервера по штрихкоду.

    Args:
        barcode: Штрихкод пробирки.
        host: IP-адрес ЛИС сервера.
        port: Порт ЛИС сервера.

    Returns:
        Словарь с данными от сервера или None при ошибке/таймауте.
    """
    url = f"http://{host}:{port}"
    payload = {"tube_barcode": barcode}

    try:
        logger.info(f"Отправка запроса на {url}, payload: {payload}")
        response = requests.get(url, json=payload, timeout=20)

        if response.status_code == 200:
            result = response.json()
            logger.info(f"Ответ для {barcode}: {result}")
            return result
        else:
            logger.error(f"Ошибка {response.status_code}: {response.text}")
            return None

    except requests.Timeout:
        logger.error(f"Таймаут при запросе баркода {barcode}")
        return None
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return None


if __name__ == "__main__":
    result = get_tube_info("3002694616")
    print(f"Результат: {result}")
