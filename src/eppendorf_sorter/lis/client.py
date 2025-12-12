"""
Клиент для работы с ЛИС через HTTP.
Использует многопоточность (ThreadPoolExecutor) для пакетной обработки запросов.
"""
import requests
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from ..domain.racks import TestType

logger = logging.getLogger("Sorter.LIS")


def get_tube_info_sync(barcode: str, host: str, port: int) -> Optional[Dict]:
    """
    Синхронный запрос информации о пробирке по баркоду.
    
    Args:
        barcode: Баркод пробирки
        host: Адрес ЛИС сервера
        port: Порт ЛИС сервера
        
    Returns:
        Словарь с информацией о пробирке или None при ошибке
    """
    url = f"http://{host}:{port}/tube/{barcode}"
    try:
        response = requests.get(url, timeout=5.0)
        if response.status_code == 200:
            return response.json()
        else:
            logger.warning(f"ЛИС вернула статус {response.status_code} для {barcode}")
            return None
    except requests.RequestException as e:
        logger.error(f"Ошибка запроса к ЛИС для {barcode}: {e}")
        return None


def parse_test_type(response: Optional[Dict]) -> TestType:
    """
    Парсит тип теста из ответа ЛИС.
    
    Args:
        response: Ответ от ЛИС сервера
        
    Returns:
        TestType enum
    """
    if response is None:
        return TestType.ERROR
    
    tests = response.get("tests", [])
    if not tests:
        return TestType.UNKNOWN
    
    has_ugi = any(t.lower() in ["ugi", "pcr-1"] for t in tests)
    has_vpch = any(t.lower() in ["vpch", "pcr-2"] for t in tests)
    
    if has_ugi and has_vpch:
        return TestType.UGI_VPCH
    elif has_ugi:
        return TestType.UGI
    elif has_vpch:
        return TestType.VPCH
    elif any("pcr" in t.lower() for t in tests):
        return TestType.OTHER
    else:
        return TestType.UNKNOWN


class LISClient:
    """
    Многопоточный клиент для работы с ЛИС.
    Позволяет делать пакетные запросы параллельно.
    """
    
    def __init__(self, host: str, port: int, max_workers: int = 20):
        """
        Args:
            host: Адрес ЛИС сервера
            port: Порт ЛИС сервера
            max_workers: Количество потоков для параллельных запросов
        """
        self.host = host
        self.port = port
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        logger.info(f"LISClient инициализирован: {host}:{port}, workers={max_workers}")
    
    def get_tube_types_batch(self, barcodes: List[str]) -> Dict[str, TestType]:
        """
        Получить типы тестов для списка баркодов параллельно.
        
        Args:
            barcodes: Список баркодов
            
        Returns:
            Словарь {barcode: TestType}
        """
        if not barcodes:
            return {}
        
        logger.info(f"Запрос типов тестов для {len(barcodes)} баркодов")
        
        # Создаём задачи для всех баркодов
        future_to_barcode = {
            self.executor.submit(get_tube_info_sync, bc, self.host, self.port): bc
            for bc in barcodes
        }
        
        results = {}
        for future in as_completed(future_to_barcode):
            barcode = future_to_barcode[future]
            try:
                response = future.result()
                test_type = parse_test_type(response)
                results[barcode] = test_type
                logger.debug(f"{barcode} -> {test_type.name}")
            except Exception as e:
                logger.error(f"Ошибка обработки {barcode}: {e}")
                results[barcode] = TestType.ERROR
        
        logger.info(f"Получены типы для {len(results)}/{len(barcodes)} баркодов")
        return results
    
    def shutdown(self):
        """Остановить executor и освободить ресурсы."""
        logger.info("Завершение работы LISClient")
        self.executor.shutdown(wait=True)
