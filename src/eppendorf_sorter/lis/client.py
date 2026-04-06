"""Клиент для работы с ЛИС через HTTP.

Использует многопоточность (ThreadPoolExecutor) для пакетной обработки запросов.
Каждый поток получает собственную requests.Session через thread-local хранилище,
что обеспечивает потокобезопасное переиспользование соединений.

API ЛИС:
    GET http://host:port
    Body (json): {"tube_barcode": "123456789"}
    Response: {"tube_barcode": "123456789", "tests": ["PCR", "PCR-1"]}
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import threading

try:
    from ..domain.racks import TestType
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from eppendorf_sorter.domain.racks import TestType


logger = logging.getLogger("Sorter.LIS")

# Thread-local хранилище: каждый поток ThreadPoolExecutor получает свою Session,
# чтобы избежать гонок при одновременных HTTP-запросах из разных потоков.
_thread_local = threading.local()


def _get_session() -> requests.Session:
    """Возвращает requests.Session для текущего потока.

    Сессия создаётся лениво при первом вызове в потоке и переиспользуется
    при последующих вызовах. Настроена с retry-политикой и отключённым
    keep-alive (Connection: close).

    Returns:
        Экземпляр requests.Session, привязанный к текущему потоку.
    """
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[502, 503, 504],
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=5,
            pool_maxsize=5,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers["Connection"] = "close"
        _thread_local.session = session
    return _thread_local.session


def get_tube_info_sync(barcode: str, host: str, port: int, timeout: float = 10.0) -> Optional[Dict]:
    """Синхронный запрос информации о пробирке по баркоду.

    Отправляет GET-запрос к ЛИС серверу с баркодом в теле JSON.

    Args:
        barcode: Баркод пробирки.
        host: Адрес ЛИС сервера.
        port: Порт ЛИС сервера.
        timeout: Таймаут запроса в секундах.

    Returns:
        Словарь с информацией о пробирке в формате
        ``{"tube_barcode": "...", "tests": ["PCR", "PCR-1", ...]}``
        или None при ошибке запроса.
    """
    url = f"http://{host}:{port}"

    payload = {
        "tube_barcode": barcode
    }

    session = _get_session()

    try:
        response = session.get(
            url,
            json=payload,
            timeout=timeout
        )

        if response.status_code == 200:
            result = response.json()
            logger.info(f"ЛИС ответ для {barcode}: {result}")
            return result
        else:
            logger.warning(f"ЛИС вернула статус {response.status_code} для {barcode}: {response.text}")
            return None

    except requests.Timeout:
        logger.error(f"Таймаут запроса к ЛИС для {barcode}")
        return None
    except requests.RequestException as e:
        logger.error(f"Ошибка запроса к ЛИС для {barcode}: {e}")
        return None


def parse_test_type(response: Optional[Dict]) -> Tuple[TestType, List[str]]:
    """Парсит тип теста из ответа ЛИС.

    Определяет TestType на основе списка тестов в ответе сервера.

    Маппинг названий тестов:
        - PCR-1 / pcr1 / ugi -> UGI
        - PCR-2 / pcr2 / vpch -> VPCH
        - PCR-1 + PCR-2 -> UGI_VPCH
        - PCR (без номера) -> OTHER

    Args:
        response: Ответ от ЛИС сервера в формате
            ``{"tube_barcode": "...", "tests": [...]}``, или None.

    Returns:
        Кортеж из двух элементов:
            - TestType: определённый тип теста (ERROR если response is None).
            - list[str]: список сырых названий тестов из ответа.
    """
    if response is None:
        return TestType.ERROR, []

    tests = response.get("tests", [])
    if not tests:
        return TestType.UNKNOWN, []

    # Сохраняем сырые тесты как список строк
    raw_tests = list(tests) if isinstance(tests, list) else [str(tests)]

    # Нормализуем названия тестов (приводим к нижнему регистру)
    tests_lower = [t.lower().strip() for t in raw_tests]

    # Проверяем наличие конкретных тестов
    has_pcr1 = any(t in ["pcr-1", "pcr1", "ugi"] for t in tests_lower)
    has_pcr2 = any(t in ["pcr-2", "pcr2", "vpch"] for t in tests_lower)
    has_pcr_generic = any(t == "pcr" for t in tests_lower)

    if has_pcr1 and has_pcr2:
        result = TestType.UGI_VPCH
    elif has_pcr1:
        result = TestType.UGI
    elif has_pcr2:
        result = TestType.VPCH
    elif has_pcr_generic:
        result = TestType.OTHER
    else:
        # Если есть какие-то тесты, но не распознаны - OTHER
        result = TestType.OTHER

    logger.info(f"parse_test_type: tests={raw_tests} -> {result.name}")
    return result, raw_tests


class LISClient:
    """Многопоточный клиент для пакетных запросов к ЛИС.

    Оборачивает синхронные HTTP-запросы в ThreadPoolExecutor,
    позволяя параллельно опрашивать ЛИС для списка баркодов.

    Attributes:
        host: Адрес ЛИС сервера.
        port: Порт ЛИС сервера.
        timeout: Таймаут одного HTTP-запроса в секундах.
        executor: Пул потоков для параллельных запросов.
    """

    def __init__(self, host: str, port: int, max_workers: int = 20, timeout: float = 10.0):
        """Инициализирует клиент и создаёт пул потоков.

        Args:
            host: Адрес ЛИС сервера.
            port: Порт ЛИС сервера.
            max_workers: Количество потоков для параллельных запросов.
            timeout: Таймаут одного запроса в секундах.
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        logger.info(f"LISClient инициализирован: {host}:{port}, workers={max_workers}, timeout={timeout}s")

    def get_tube_info(self, barcode: str) -> Optional[Dict]:
        """Получает информацию о пробирке по баркоду.

        Args:
            barcode: Баркод пробирки.

        Returns:
            Словарь с информацией о пробирке или None при ошибке.
        """
        return get_tube_info_sync(barcode, self.host, self.port, self.timeout)

    def get_tube_type(self, barcode: str) -> TestType:
        """Определяет тип теста для пробирки.

        Args:
            barcode: Баркод пробирки.

        Returns:
            Значение перечисления TestType.
        """
        response = self.get_tube_info(barcode)
        test_type, _ = parse_test_type(response)
        return test_type

    def get_tube_types_batch(self, barcodes: List[str]) -> Dict[str, TestType]:
        """Получает типы тестов для списка баркодов параллельно.

        Отправляет запросы к ЛИС для всех баркодов одновременно
        через ThreadPoolExecutor. При ошибке отдельного запроса
        соответствующий баркод получает тип TestType.ERROR.

        Args:
            barcodes: Список баркодов пробирок.

        Returns:
            Словарь, где ключ — баркод, значение — определённый TestType.
        """
        if not barcodes:
            return {}

        logger.info(f"Запрос типов тестов для {len(barcodes)} баркодов")

        # Создаём задачи для всех баркодов
        future_to_barcode = {
            self.executor.submit(get_tube_info_sync, bc, self.host, self.port, self.timeout): bc
            for bc in barcodes
        }

        results = {}
        for future in as_completed(future_to_barcode):
            barcode = future_to_barcode[future]
            try:
                response = future.result()
                test_type, _ = parse_test_type(response)
                results[barcode] = test_type
                logger.debug(f"{barcode} -> {test_type.name}")
            except Exception as e:
                logger.error(f"Ошибка обработки {barcode}: {e}")
                results[barcode] = TestType.ERROR

        logger.info(f"Получены типы для {len(results)}/{len(barcodes)} баркодов")
        return results

    def shutdown(self):
        """Останавливает executor и освобождает ресурсы."""
        logger.info("Завершение работы LISClient")
        self.executor.shutdown(wait=False)

if __name__ == "__main__":
    response = get_tube_info_sync('3002694616', "192.168.12.80", 7117)
    test_type, raw_tests = parse_test_type(response)
    print(f"Ответ: {response}")
    print(f"Тип теста: {test_type.name}, сырые тесты: {raw_tests}")
