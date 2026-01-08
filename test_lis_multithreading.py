import urllib.request
import urllib.error
import json
import time
import threading
import random

SERVER_URL = "http://localhost:7114"

def make_request(barcode):
    url = f"{SERVER_URL}/tube/{barcode}"
    try:
        start_time = time.time()
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode('utf-8'))
            elapsed = time.time() - start_time
            
            print(f"[OK] {barcode}: {data.get('tests')} ({elapsed:.3f}s)")
            
    except urllib.error.HTTPError as e:
        print(f"[Error] {barcode}: HTTP {e.code}")
    except Exception as e:
        print(f"[Fail] {barcode}: {e}")

def run_load_test(count=10):
    threads = []
    print(f"Запуск {count} параллельных запросов...")
    
    for i in range(count):
        # Генерируем случайный баркод
        barcode = f"BARCODE-{random.randint(1000, 9999)}"
        t = threading.Thread(target=make_request, args=(barcode,))
        threads.append(t)
        t.start()
        # Небольшая пауза, чтобы не заспамить консоль мгновенно, 
        # но достаточно быстро для проверки многопоточности
        time.sleep(0.01) 

    for t in threads:
        t.join()

if __name__ == "__main__":
    # 1. Проверка Health check
    print("--- Проверка Health ---")
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/health") as res:
            print(json.loads(res.read().decode()))
    except:
        print("Сервер не запущен!")
        exit(1)

    # 2. Нагрузочный тест
    print("\n--- Проверка запросов ---")
    run_load_test(200)