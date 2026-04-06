"""
Тестовый ЛИС сервер для локальной отладки.

Принимает GET-запросы с JSON body: {"tube_barcode": "123456789"}
Отвечает JSON: {"tube_barcode": "123456789", "tests": ["PCR-1", "PCR-2", ...]}

Типы тестов назначаются случайно с настраиваемыми весами.

Запуск:
    python test_server.py
    python test_server.py --port 7117
    python test_server.py --port 7117 --delay 0.5
"""

import json
import random
import time
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer

# Варианты ответов и их веса (вероятности)
TEST_VARIANTS = [
    (["PCR-1"],          30),   # UGI
    (["PCR-2"],          30),   # VPCH
    (["PCR-1", "PCR-2"], 20),   # UGI_VPCH
    (["PCR"],            20),   # OTHER
]

TESTS_LIST = [v[0] for v in TEST_VARIANTS]
WEIGHTS = [v[1] for v in TEST_VARIANTS]

# Настройки задержки (имитация реального сервера)
RESPONSE_DELAY_MIN = 0.0
RESPONSE_DELAY_MAX = 0.0


class LISHandler(BaseHTTPRequestHandler):
    """HTTP-обработчик, имитирующий поведение ЛИС сервера.

    Принимает GET-запросы с JSON-телом, содержащим штрихкод пробирки,
    и возвращает случайно выбранный набор тестов согласно настроенным весам.
    """

    def do_GET(self):
        """Обрабатывает GET-запрос с JSON body вида {"tube_barcode": "..."}.

        Извлекает штрихкод из тела запроса, опционально симулирует задержку
        ответа и отвечает JSON с назначенным набором тестов.
        """
        content_length = int(self.headers.get("Content-Length", 0))

        if content_length == 0:
            self._send_error(400, "Отсутствует JSON body с tube_barcode")
            return

        raw_body = self.rfile.read(content_length)

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            self._send_error(400, "Невалидный JSON")
            return

        barcode = body.get("tube_barcode")
        if not barcode:
            self._send_error(400, "Поле tube_barcode обязательно")
            return

        # Имитация задержки
        if RESPONSE_DELAY_MAX > 0:
            delay = random.uniform(RESPONSE_DELAY_MIN, RESPONSE_DELAY_MAX)
            time.sleep(delay)

        # Генерируем ответ
        tests = random.choices(TESTS_LIST, weights=WEIGHTS, k=1)[0]

        response = {
            "tube_barcode": barcode,
            "tests": tests,
        }

        self._send_json(200, response)

        tests_str = ", ".join(tests) if tests else "(пусто)"
        print(f"  [{barcode}] -> {tests_str}")

    def _send_json(self, code: int, data: dict) -> None:
        """Отправляет HTTP-ответ с JSON-телом.

        Args:
            code: HTTP-статус ответа.
            data: Данные для сериализации в JSON.
        """
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str) -> None:
        """Отправляет JSON-ответ с описанием ошибки.

        Args:
            code: HTTP-статус ошибки.
            message: Текст сообщения об ошибке.
        """
        self._send_json(code, {"error": message})

    def log_message(self, format, *args) -> None:
        # Подавляем стандартный лог каждого запроса — он слишком шумный для отладки
        pass


def main() -> None:
    """Парсит аргументы командной строки и запускает тестовый HTTP-сервер."""
    parser = argparse.ArgumentParser(description="Тестовый ЛИС сервер")
    parser.add_argument("--host", default="0.0.0.0", help="Адрес (по умолчанию 0.0.0.0)")
    parser.add_argument("--port", type=int, default=7117, help="Порт (по умолчанию 7117)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Макс. задержка ответа в секундах (по умолчанию 0)")
    args = parser.parse_args()

    global RESPONSE_DELAY_MAX
    RESPONSE_DELAY_MAX = args.delay

    server = ThreadingHTTPServer((args.host, args.port), LISHandler)

    print(f"Тестовый ЛИС сервер запущен на http://{args.host}:{args.port}")
    print(f"Задержка ответа: 0 - {args.delay}с")
    print(f"Веса: PCR-1={WEIGHTS[0]}%, PCR-2={WEIGHTS[1]}%, "
          f"PCR-1+PCR-2={WEIGHTS[2]}%, PCR={WEIGHTS[3]}%")
    print("-" * 50)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен")
        server.server_close()


if __name__ == "__main__":
    main()
