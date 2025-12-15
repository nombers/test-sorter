**Краткий обзор**: Этот репозиторий реализует контроллер сборочной ячейки сортировки пробирок.
- **Запуск**: `python main.py` (вызов `run_workcell` в [main.py](main.py)).
- **Архитектура**: ключевые модули - `orchestration` (инициализация и основной цикл), `devices` (робот, сканер), `domain` (логика штативов/паллетов), `lis` (клиент к ЛИС), `config` (yaml-конфиги).

**Ключевые файлы и назначение**:
- `src/eppendorf_sorter/orchestration/bootstrap.py`: сборка и запуск рабочих потоков, инициализация логов и устройств.
- `src/eppendorf_sorter/orchestration/robot_logic.py`: основная логика робота — двухфазный цикл `scan -> sort`, `RobotThread` использует `stop_event`.
- `src/eppendorf_sorter/orchestration/robot_protocol.py`: константы SR/NR для взаимодействия с контроллером робота (регистр-ориентированный протокол).
- `src/eppendorf_sorter/devices/robots/robot_agilebot.py`: обёртка SDK Agilebot; использует `Arm` API и предоставляет методы `set_string_register`, `get_number_register` и т.п.
- `src/eppendorf_sorter/devices/scanners/scanner_hikrobot_tcp.py`: TCP-сканер, команда `start`/`stop`, `scan(timeout)` возвращает `(barcode, recv_time)`.
- `src/eppendorf_sorter/domain/racks.py`: логика управления паллетами/штативами, `RackSystemManager` — thread-safe координация.
- `src/eppendorf_sorter/lis/client.py`: многопоточный HTTP-клиент к ЛИС (`get_tube_types_batch`).
- `src/eppendorf_sorter/config/robot.yaml` и `load_robot_config()` — место конфигов (IP, порт, timeout).

**Почему такое устройство**:
- Проект явно ориентирован на работу с реальным модульным железом (Agilebot, Hikrobot) — код содержит активное взаимодействие через регистры/сокеты.
- Отделение `domain` от `devices` и `orchestration` позволяет легко писать тесты или мок-совместимые имплементации для устройств.

**Ключевые API / взаимодействия (примеры)**:
- Команды роботу: `self.robot.set_string_register(SR.iteration_type, SR_VAL.scanning)`
- Запуск итерации: `self.robot.set_number_register(NR.iteration_starter, NR_VAL.start)`
- Ожидание статусов: `self._wait_until(lambda: self.robot.get_number_register(NR.scan_status) != NR_VAL.scan_reset)`
- Сканирование: `barcode, t = self.scanner.scan(timeout)` (возвращает `"NoRead"` если нет данных).
- Запрос к ЛИС: `barcode_to_test_type = lis_client.get_tube_types_batch(all_barcodes)`.

**Паттерны и соглашения**:
- Thread-safety: `RackSystemManager` и `BaseRack` используют блокировки; любое изменение состояния штатива должно проходить через их методы.
- Shutdown: глобальное `stop_event` (threading.Event) распространяется в `RobotThread` — корректное завершение требует проверки `stop_event` и вызова `shutdown` в `bootstrap`.
- Конфиги: `yaml` в `config/` — используйте `load_*_config` для доступа к полям; не хардкодьте IP/ports.
- Логи: используйте `create_logger` (лог-файлы — `logs/YYYY-MM-DD/<name>.log`).

**Разработка, тестирование и отладка**:
- Быстрый запуск (на машине с доступом к железу):
  - Создать виртуальное окружение и установить зависимости: `python -m venv .venv` + `pip install -r requirements.txt`.
  - Запуск: `python main.py`.
- Запуск в режиме без железа: создавайте моки для `CellRobot` и `Scanner` и передавайте их в `RobotThread` (см. `RobotThread.__init__`). Примеры:
  - Стабы: создайте `MockRobot` и `MockScanner` с методами `connect()/disconnect()/set_string_register()/get_number_register()` и `scan()`.
- Тесты: `pytest` присутствует в `requirements.txt`, но репозиторий не содержит тестов; при добавлении тестов старайтесь изолировать железные интеграции (внешние вызовы в `devices` и `lis`) и тестировать `domain` и `orchestration` с моками.
- Локальная эмуляция ЛИС: можно заменить `LISClient` на локальный тестовый web-сервер (например, Flask) возвратом JSON `/tube/<barcode>`.

**Примеры часто-используемых сценариев для AI**:
- Добавление нового номера регистра: обновите `robot_protocol.py` и используйте `NR`/`SR` именно оттуда по всему коду — это минимизирует регистровые ошибки.
- Добавление нового типа теста: добавить в `TestType` в `domain/racks.py`, затем убедиться, что `LISClient.parse_test_type` и `initialize_destination_racks` поддерживают новый тип.
- Изменение целевой логики заполнения: правки в `RackSystemManager.find_available_rack` и `DestinationRack.reached_target`.

**Что стоит помнить**:
- Все сетевые/железные вызовы могут быть медленными / бросать исключения — оборачивайте их и логируйте ошибку.
- `RobotThread` использует `self.stop_event` — любые долгие циклы/ожидания должны проверять этот флаг.
- Не изменяйте `robot_protocol.py` значения без согласования с программой робота (контроллером).
- Код содержит TODO (в `robot_logic.py`) для интеграции со стороны оператора — при добавлении UI/сигналов используйте `RackOccupancy.WAITING_REPLACE`.

**Куда смотреть при добавлении фич**:
- `src/eppendorf_sorter/orchestration/robot_logic.py` — основной рабочий поток; здесь чаще всего вы будете расширять сценарии сортировки/сканирования.
- `src/eppendorf_sorter/domain/racks.py` — состояние штативов и все проверки/блокировки, изменение сюда должно быть thread-safe.
- `src/eppendorf_sorter/devices/*` — обёртки устройств; добавляйте только интерфейсные методы, реализация работает через SDK/сокеты.

Если что-то в этом описании неясно или нужна дополнительная информация (mock-helpers, тесты, внутренние API), напишите — я дополню инструкцию.
