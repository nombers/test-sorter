"""
Тестовый HTTP-сервер для эмуляции ответов ЛИС.
Многопоточный сервер на стандартной библиотеке Python (без внешних зависимостей).

Возвращает рандомные типы тестов: pcr-1 (УГИ), pcr-2 (ВПЧ), pcr (Разное)

Эндпоинты:
    GET /tube/<barcode> - получить информацию о пробирке (используется в client.py)
    GET /health - проверка работоспособности сервера
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import json
import random
import logging
from datetime import datetime
import time
import threading
import re

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Счётчик запросов (thread-safe)
request_counter = {"count": 0}
counter_lock = threading.Lock()


def get_request_count() -> int:
    """Получить и инкрементировать счётчик запросов (thread-safe)"""
    with counter_lock:
        request_counter["count"] += 1
        return request_counter["count"]


class LISRequestHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов для тестового ЛИС сервера"""
    
    # Отключаем стандартный лог каждого запроса
    def log_message(self, format, *args):
        pass
    
    def _send_json_response(self, data: dict, status: int = 200):
        """Отправить JSON-ответ"""
        response = json.dumps(data, ensure_ascii=False)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(response.encode('utf-8')))
        self.end_headers()
        self.wfile.write(response.encode('utf-8'))
    
    def do_GET(self):
        """Обработка GET-запросов"""
        
        # GET /tube/<barcode>
        tube_match = re.match(r'^/tube/(.+)$', self.path)
        if tube_match:
            barcode = tube_match.group(1)
            self._handle_get_tube(barcode)
            return
        
        # GET /health
        if self.path == '/health':
            self._handle_health()
            return
        
        # GET /
        if self.path == '/':
            self._handle_index()
            return
        
        # 404
        self._send_json_response({
            "status": "error",
            "message": f"Endpoint not found: {self.path}"
        }, 404)
    
    def _handle_get_tube(self, barcode: str):
        """Получить информацию о пробирке по баркоду"""
        req_num = get_request_count()
        
        logger.info(f"[#{req_num}] Запрос: barcode={barcode}")
        
        if not barcode:
            logger.warning(f"[#{req_num}] Пустой баркод")
            self._send_json_response({
                "status": "error",
                "error_code": "MISSING_BARCODE",
                "message": "Не указан штрихкод"
            }, 400)
            return
        
        # Имитация задержки сети/БД (иногда)
        delay = random.choice([0, 0, 0, 0, 0.1, 0.2])
        if delay > 0:
            time.sleep(delay)
        
        # Случайный выбор типа теста
        response_type = random.choice([
            "pcr-1",           # только УГИ
            "pcr-2",           # только ВПЧ
            "pcr-1+pcr-2",     # УГИ + ВПЧ
            "pcr"              # разное
        ])
        
        # Формирование ответа в формате, ожидаемом client.py
        if response_type == "pcr-1":
            tests = ["pcr-1"]
        elif response_type == "pcr-2":
            tests = ["pcr-2"]
        elif response_type == "pcr-1+pcr-2":
            tests = ["pcr-1", "pcr-2"]
        else:  # pcr
            tests = ["pcr"]
        
        response_data = {
            "barcode": barcode,
            "tests": tests
        }
        
        logger.info(f"[#{req_num}] Ответ: barcode={barcode}, tests={tests}")
        
        self._send_json_response(response_data, 200)
    
    def _handle_health(self):
        """Проверка работоспособности сервера"""
        with counter_lock:
            count = request_counter["count"]
        
        self._send_json_response({
            "status": "ok",
            "server": "Test LIS Server (stdlib/Threaded)",
            "requests_processed": count,
            "timestamp": datetime.now().isoformat()
        }, 200)
    
    def _handle_index(self):
        """Главная страница с информацией о сервере"""
        self._send_json_response({
            "name": "Test LIS Server",
            "version": "1.0",
            "endpoints": {
                "GET /tube/<barcode>": "Получить тип теста для пробирки",
                "GET /health": "Проверка работоспособности"
            }
        }, 200)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Многопоточный HTTP-сервер"""
    daemon_threads = True  # Потоки завершаются вместе с основным


def run_server(host: str = '0.0.0.0', port: int = 7114):
    """
    Запуск сервера.
    
    Args:
        host: Адрес для прослушивания
        port: Порт сервера
    """
    print("=" * 70)
    print("ТЕСТОВЫЙ СЕРВЕР ЛИС (stdlib/ThreadingMixIn)")
    print("=" * 70)
    print(f"\nАдрес: http://{host}:{port}")
    print("\nЭндпоинты:")
    print(f"  GET /tube/<barcode> - информация о пробирке")
    print(f"  GET /health         - статус сервера")
    print("=" * 70 + "\n")
    
    server = ThreadedHTTPServer((host, port), LISRequestHandler)
    
    try:
        logger.info(f"Сервер запущен на {host}:{port}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nОстановка сервера...")
        server.shutdown()
        logger.info("Сервер остановлен")


if __name__ == '__main__':
    run_server()