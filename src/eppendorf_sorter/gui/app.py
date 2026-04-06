# src/eppendorf_sorter/gui/app.py
"""GUI приложение для управления роботом-сортировщиком Eppendorf.

Модуль реализует графический интерфейс на базе Tkinter для управления
процессом сортировки пробирок типа эппендорф. Включает главное окно
приложения и диалог подтверждения замены штатива.

Функционал:
    - Запуск/остановка/пауза программы.
    - Просмотр баркодов и типов анализов в штативах.
    - Настройка целевых штативов (количество ячеек).
    - Отображение прогресса и состояния штативов в виде матрицы ячеек.
    - Диалог замены штатива со схемой расположения на столе робота.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from enum import Enum
from typing import Optional, Callable, Dict

from src.eppendorf_sorter.domain.racks import (
    RackSystemManager,
    TestType,
    TubeInfo,
    SourceRack,
    DestinationRack,
)


# ==================== КОНСТАНТЫ СТИЛЕЙ ====================

COLORS = {
    "red": "#c02524",
    "blue": "#006a83",
    "dark_gray": "#9d9d9d",
    "light_gray": "#ededed",
    "black": "#000000",
    "white": "#ffffff",
    "green": "#28a745",
    "yellow": "#ffc107",
    "orange": "#fd7e14",
}

FONT_FAMILY = "Montserrat"

# Fallback шрифты если Montserrat недоступен
FONTS = {
    "title": (FONT_FAMILY, 18, "bold"),
    "header": (FONT_FAMILY, 14, "bold"),
    "normal": (FONT_FAMILY, 11),
    "small": (FONT_FAMILY, 10),
    "button": (FONT_FAMILY, 12, "bold"),
    "status": (FONT_FAMILY, 16, "bold"),
    "log": ("Consolas", 10),
}


class AppStatus(Enum):
    """Статусы приложения сортировщика.

    Определяет возможные состояния работы приложения,
    влияющие на доступность кнопок управления и отображение
    в статус-баре.

    Attributes:
        STOPPED: Программа остановлена, доступен только запуск.
        RUNNING: Программа работает, доступны пауза и остановка.
        PAUSED: Программа на паузе, доступны продолжение и остановка.
        WAITING: Ожидание замены штатива, доступна кнопка подтверждения замены.
    """
    STOPPED = "ОСТАНОВЛЕН"
    RUNNING = "РАБОТАЕТ"
    PAUSED = "ПАУЗА"
    WAITING = "ОЖИДАНИЕ"


# ==================== ДИАЛОГ ЗАМЕНЫ ШТАТИВА ====================

class RackReplacementDialog(tk.Toplevel):
    """Полноэкранный диалог со схемой расположения штативов на роботе.

    Показывает визуальную схему стола робота с подсветкой штативов,
    которые необходимо заменить. Пользователь подтверждает замену
    кнопкой или отменяет операцию.

    Attributes:
        confirmed: Флаг подтверждения замены пользователем.
        SOURCE_LAYOUT: Расположение исходных штативов (id -> (номер, название)).
        OTHER_RACK: Кортеж (id, название) для штатива типа "Общее".
        DEST_RACKS: Список кортежей (id, тип) целевых штативов в порядке
            отображения слева направо.
    """

    # Расположение штативов на столе робота
    # Верхняя часть (сетка 3 строки x 2 колонки):
    #   col 0: РОБОТ (rowspan=3)
    #   col 1: Исходный 1, Исходный 2, Общее (#3)
    # Нижняя часть (1 строка x 6 колонок):
    #   #9 UGI+VPCH, #8 UGI+VPCH, #7 VPCH, #6 VPCH, #5 UGI, #4 UGI

    SOURCE_LAYOUT = {
        0: (1, "Исходный 1"),
        1: (2, "Исходный 2"),
    }

    # Общее (rack #3) — отдельно, ряд 2 верхней части
    OTHER_RACK = (3, "Общее")

    # Целевые штативы (нижняя часть, слева направо)
    DEST_RACKS = [
        (9, "UGI+VPCH"),
        (8, "UGI+VPCH"),
        (7, "VPCH"),
        (6, "VPCH"),
        (5, "UGI"),
        (4, "UGI"),
    ]

    def __init__(self, parent: tk.Tk, highlight_rack_ids: list, reason: str = ""):
        """Инициализация диалога замены штатива.

        Args:
            parent: Родительское окно Tkinter.
            highlight_rack_ids: Список идентификаторов штативов, которые
                необходимо подсветить зелёным цветом на схеме.
            reason: Текстовая причина ожидания замены, отображается
                как подзаголовок диалога.
        """
        super().__init__(parent)
        self.confirmed = False
        self._highlight_ids = set(highlight_rack_ids)

        # Полноэкранное окно (на весь рабочий стол Windows)
        self.title("Замена штатива")
        self.configure(bg=COLORS["white"])
        self.state("zoomed")
        self.attributes("-topmost", True)
        self.grab_set()
        self.focus_set()

        # Закрытие по Escape
        self.bind("<Escape>", lambda e: self.destroy())

        self._create_content(reason)

    def _create_content(self, reason: str):
        """Создание содержимого диалога: заголовок, схема и кнопки.

        Args:
            reason: Причина ожидания замены для отображения пользователю.
        """
        # --- Кнопки внизу ---
        # Пакуем ПЕРВЫМИ с side=BOTTOM, чтобы гарантировать видимость
        # при любом размере окна (pack выделяет место в порядке вызовов)
        buttons_frame = tk.Frame(self, bg=COLORS["white"])
        buttons_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 20))

        # --- Заголовок ---
        header = tk.Label(
            self,
            text="ЗАМЕНА ШТАТИВА",
            font=(FONT_FAMILY, 28, "bold"),
            bg=COLORS["white"],
            fg=COLORS["red"],
        )
        header.pack(pady=(10, 5))

        # --- Причина ---
        if reason:
            reason_label = tk.Label(
                self,
                text=reason,
                font=(FONT_FAMILY, 16),
                bg=COLORS["white"],
                fg=COLORS["black"],
            )
            reason_label.pack(pady=(0, 5))

        # --- Подсказка ---
        hint = tk.Label(
            self,
            text="Замените штативы, выделенные ЗЕЛЁНЫМ цветом, и нажмите кнопку подтверждения",
            font=(FONT_FAMILY, 13),
            bg=COLORS["white"],
            fg=COLORS["dark_gray"],
        )
        hint.pack(pady=(0, 10))

        # --- Схема стола робота ---
        scheme_frame = tk.Frame(self, bg=COLORS["white"])
        scheme_frame.pack(expand=True, fill=tk.BOTH, padx=60, pady=5)

        self._draw_scheme(scheme_frame)

        # --- Кнопки подтверждения/отмены ---
        buttons_inner = tk.Frame(buttons_frame, bg=COLORS["white"])
        buttons_inner.pack()

        confirm_btn = tk.Button(
            buttons_inner,
            text="ШТАТИВ ЗАМЕНЁН",
            font=(FONT_FAMILY, 20, "bold"),
            bg=COLORS["green"],
            fg=COLORS["white"],
            activebackground="#1e7e34",
            activeforeground=COLORS["white"],
            padx=40,
            pady=15,
            relief=tk.FLAT,
            cursor="hand2",
            command=self._on_confirm,
        )
        confirm_btn.pack(side=tk.LEFT, padx=20)

        cancel_btn = tk.Button(
            buttons_inner,
            text="ОТМЕНА",
            font=(FONT_FAMILY, 20, "bold"),
            bg=COLORS["dark_gray"],
            fg=COLORS["white"],
            activebackground="#7d7d7d",
            activeforeground=COLORS["white"],
            padx=40,
            pady=15,
            relief=tk.FLAT,
            cursor="hand2",
            command=self.destroy,
        )
        cancel_btn.pack(side=tk.LEFT, padx=20)

    def _draw_scheme(self, parent: tk.Frame):
        """Нарисовать схему расположения штативов на столе робота.

        Схема состоит из двух частей:
        - Верхняя: блок "РОБОТ" слева, исходные штативы и "Общее" справа.
        - Нижняя: 6 целевых штативов в один ряд.

        Args:
            parent: Родительский фрейм для размещения схемы.
        """
        # --- Верхняя часть: РОБОТ + Исходные + Общее ---
        top_frame = tk.Frame(parent, bg=COLORS["white"])
        top_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # 2 колонки: робот (weight=1) и штативы справа (weight=2)
        top_frame.columnconfigure(0, weight=1, uniform="top")
        top_frame.columnconfigure(1, weight=2, uniform="top")
        for row in range(3):
            top_frame.rowconfigure(row, weight=1, uniform="toprow")

        # Робот — занимает 3 ряда слева
        robot_frame = tk.Frame(
            top_frame, bg="#e0e0e0", relief=tk.RIDGE, bd=2,
        )
        robot_frame.grid(row=0, column=0, rowspan=3, padx=8, pady=8, sticky="nsew")
        tk.Label(
            robot_frame, text="РОБОТ",
            font=(FONT_FAMILY, 28, "bold"),
            bg="#e0e0e0", fg=COLORS["dark_gray"],
        ).pack(expand=True)

        # Исходные штативы (ряды 0, 1) — справа, без подсветки
        for row_idx, (rack_id, label) in self.SOURCE_LAYOUT.items():
            self._create_rack_cell(
                top_frame, row_idx, 1, rack_id, label,
                highlighted=False, is_source=True,
            )

        # Общее (#3)
        rack_id, label = self.OTHER_RACK
        is_highlighted = rack_id in self._highlight_ids
        self._create_rack_cell(
            top_frame, 2, 1, rack_id, label,
            highlighted=is_highlighted, is_source=False,
        )

        # --- Нижняя часть: 6 целевых штативов в ряд ---
        bottom_frame = tk.Frame(parent, bg=COLORS["white"])
        bottom_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        for col in range(6):
            bottom_frame.columnconfigure(col, weight=1, uniform="bot")
        bottom_frame.rowconfigure(0, weight=1)

        for col, (rack_id, label) in enumerate(self.DEST_RACKS):
            is_highlighted = rack_id in self._highlight_ids
            self._create_rack_cell(
                bottom_frame, 0, col, rack_id, label,
                highlighted=is_highlighted, is_source=False,
            )

    def _create_rack_cell(self, parent: tk.Frame, row: int, col: int,
                          rack_id: int, label: str,
                          highlighted: bool = False, is_source: bool = False):
        """Создать ячейку штатива на схеме расположения.

        Цвет ячейки определяется по приоритету: подсвеченные штативы
        отображаются зелёным, исходные — голубым, остальные — серым.

        Args:
            parent: Родительский фрейм-сетка.
            row: Номер строки в сетке.
            col: Номер столбца в сетке.
            rack_id: Идентификатор штатива для отображения.
            label: Текстовая метка типа штатива.
            highlighted: Подсветить ячейку зелёным (штатив требует замены).
            is_source: Является ли штатив исходным (голубая подсветка).
        """
        if highlighted:
            bg_color = "#28a745"  # Зелёный
            fg_color = COLORS["white"]
            border_color = "#1e7e34"
        elif is_source:
            bg_color = "#d6eaf8"  # Голубоватый
            fg_color = COLORS["black"]
            border_color = COLORS["blue"]
        else:
            bg_color = COLORS["light_gray"]
            fg_color = COLORS["black"]
            border_color = COLORS["dark_gray"]

        cell = tk.Frame(
            parent,
            bg=bg_color,
            relief=tk.RIDGE,
            bd=2,
            highlightbackground=border_color,
            highlightthickness=2,
        )
        cell.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")

        # Номер штатива
        tk.Label(
            cell,
            text=f"#{rack_id}",
            font=(FONT_FAMILY, 28, "bold"),
            bg=bg_color,
            fg=fg_color,
        ).pack(expand=True, pady=(15, 0))

        # Тип
        tk.Label(
            cell,
            text=label,
            font=(FONT_FAMILY, 18),
            bg=bg_color,
            fg=fg_color,
        ).pack(expand=True, pady=(0, 15))

    def _on_confirm(self):
        """Обработчик подтверждения замены штатива.

        Устанавливает флаг ``confirmed`` и закрывает диалог.
        """
        self.confirmed = True
        self.destroy()


# ==================== ГЛАВНОЕ ОКНО ====================

class SorterGUI:
    """Главное окно GUI приложения сортировщика пробирок.

    Предоставляет интерфейс для управления процессом сортировки:
    кнопки старт/стоп/пауза, отображение исходных и целевых штативов,
    прогресс-бары и диалог замены штатива.

    Интеграция с RobotThread осуществляется через callback-функции:
        - set_start_callback() — запуск робота.
        - set_stop_callback() — остановка робота.
        - set_pause_callback() — пауза робота.
        - set_resume_callback() — продолжение работы робота.
        - set_rack_replaced_callback() — подтверждение замены штатива.

    Attributes:
        root: Корневое окно Tkinter.
    """

    def __init__(self, root: tk.Tk):
        """Инициализация главного окна приложения.

        Создаёт все виджеты интерфейса, настраивает стили и запускает
        периодическое обновление отображения штативов.

        Args:
            root: Корневое окно Tkinter, в котором строится интерфейс.
        """
        self.root = root
        self.root.title("Сортировщик пробирок типа эппедорф")
        self.root.configure(bg=COLORS["light_gray"])
        self.root.state("zoomed")  # Открытие во весь экран

        # --- Состояние ---
        self._status = AppStatus.STOPPED
        self._rack_manager: Optional[RackSystemManager] = None
        self._waiting_reason: str = ""  # Причина ожидания (для диалога замены)

        # --- Callbacks для интеграции с RobotThread ---
        self._start_callback: Optional[Callable] = None
        self._stop_callback: Optional[Callable] = None
        self._pause_callback: Optional[Callable] = None
        self._resume_callback: Optional[Callable] = None
        self._rack_replaced_callback: Optional[Callable] = None

        # --- Виджеты для обновления ---
        self._pallet_trees: Dict[int, ttk.Treeview] = {}
        self._rack_labels: Dict[int, tk.Label] = {}
        self._rack_target_entries: Dict[int, tk.Entry] = {}
        self._rack_target_vars: Dict[int, tk.StringVar] = {}
        self._rack_matrices: Dict[int, Dict] = {}  # Матрицы ячеек для штативов
        self._rack_matrix_frames: Dict[int, tk.Frame] = {}  # Фреймы матриц для перестроения

        # --- Создание интерфейса ---
        self._setup_styles()
        self._create_widgets()

        # --- Периодическое обновление ---
        self._schedule_updates()

    # ==================== НАСТРОЙКА СТИЛЕЙ ====================

    def _setup_styles(self):
        """Настройка стилей ttk-виджетов.

        Конфигурирует стили для кнопок управления, таблиц Treeview,
        вкладок Notebook и прогресс-баров.
        """
        style = ttk.Style()
        style.theme_use('clam')

        # Кнопки
        style.configure(
            "Start.TButton",
            background=COLORS["blue"],
            foreground=COLORS["white"],
            font=FONTS["button"],
            padding=(20, 10),
        )
        style.map("Start.TButton",
            background=[("active", "#005a73"), ("disabled", COLORS["dark_gray"])]
        )

        style.configure(
            "Stop.TButton",
            background=COLORS["red"],
            foreground=COLORS["white"],
            font=FONTS["button"],
            padding=(20, 10),
        )
        style.map("Stop.TButton",
            background=[("active", "#a01f1e"), ("disabled", COLORS["dark_gray"])]
        )

        style.configure(
            "Pause.TButton",
            background=COLORS["dark_gray"],
            foreground=COLORS["white"],
            font=FONTS["button"],
            padding=(20, 10),
        )
        style.map("Pause.TButton",
            background=[("active", "#7d7d7d"), ("disabled", COLORS["light_gray"])]
        )

        style.configure(
            "Resume.TButton",
            background=COLORS["green"],
            foreground=COLORS["white"],
            font=FONTS["button"],
            padding=(20, 10),
        )
        style.map("Resume.TButton",
            background=[("active", "#1e7e34"), ("disabled", COLORS["dark_gray"])]
        )

        # Treeview
        style.configure(
            "Pallet.Treeview",
            font=FONTS["small"],
            rowheight=25,
        )
        style.configure(
            "Pallet.Treeview.Heading",
            font=FONTS["normal"],
        )

        # Notebook вкладки — высота как у 1 ячейки матрицы
        style.configure(
            "TNotebook.Tab",
            font=(FONT_FAMILY, 18, "bold"),
            padding=(10, 12),
        )

        # Вкладки целевых штативов — шрифт поменьше, размер вкладки тот же
        style.configure(
            "Racks.TNotebook.Tab",
            font=(FONT_FAMILY, 14, "bold"),
            padding=(10, 12),
        )

        # Прогресс-бары
        style.configure(
            "Scan.Horizontal.TProgressbar",
            troughcolor=COLORS["light_gray"],
            background=COLORS["blue"],
        )
        style.configure(
            "Sort.Horizontal.TProgressbar",
            troughcolor=COLORS["light_gray"],
            background=COLORS["green"],
        )

    # ==================== СОЗДАНИЕ ВИДЖЕТОВ ====================

    def _create_widgets(self):
        """Создание всех виджетов главного окна.

        Собирает интерфейс из трёх частей: статус-бар сверху,
        панель кнопок управления, основная область с исходными
        и целевыми штативами.
        """
        # Главный контейнер
        main_frame = tk.Frame(self.root, bg=COLORS["light_gray"])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Статус-бар
        self._create_status_bar(main_frame)

        # Кнопки управления
        self._create_control_buttons(main_frame)

        # Основная область (исходные штативы + целевые штативы)
        self._create_main_area(main_frame)

    def _create_status_bar(self, parent: tk.Frame):
        """Создание статус-бара с текущим состоянием приложения.

        Args:
            parent: Родительский фрейм для размещения статус-бара.
        """
        status_frame = tk.Frame(parent, bg=COLORS["white"], height=50)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        status_frame.pack_propagate(False)

        self._status_label = tk.Label(
            status_frame,
            text=f"СТАТУС: {self._status.value}",
            font=FONTS["status"],
            bg=COLORS["white"],
            fg=COLORS["dark_gray"],
        )
        self._status_label.pack(expand=True)

    def _create_control_buttons(self, parent: tk.Frame):
        """Создание панели кнопок управления.

        Включает кнопки: СТАРТ, ПАУЗА, ПРОДОЛЖИТЬ, СТОП и ШТАТИВ ЗАМЕНЁН.
        Доступность кнопок управляется через ``_update_buttons_state``.

        Args:
            parent: Родительский фрейм для размещения панели кнопок.
        """
        buttons_frame = tk.Frame(parent, bg=COLORS["light_gray"])
        buttons_frame.pack(fill=tk.X, pady=(0, 10))

        # Кнопка СТАРТ
        self._start_btn = ttk.Button(
            buttons_frame,
            text="▶  СТАРТ",
            style="Start.TButton",
            command=self._on_start_click,
        )
        self._start_btn.pack(side=tk.LEFT, padx=5)

        # Кнопка ПАУЗА
        self._pause_btn = ttk.Button(
            buttons_frame,
            text="⏸  ПАУЗА",
            style="Pause.TButton",
            command=self._on_pause_click,
            state=tk.DISABLED,
        )
        self._pause_btn.pack(side=tk.LEFT, padx=5)

        # Кнопка ПРОДОЛЖИТЬ
        self._resume_btn = ttk.Button(
            buttons_frame,
            text="▶▶  ПРОДОЛЖИТЬ",
            style="Resume.TButton",
            command=self._on_resume_click,
            state=tk.DISABLED,
        )
        self._resume_btn.pack(side=tk.LEFT, padx=5)

        # Кнопка СТОП
        self._stop_btn = ttk.Button(
            buttons_frame,
            text="■  СТОП",
            style="Stop.TButton",
            command=self._on_stop_click,
            state=tk.DISABLED,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=5)

        # Кнопка ЗАМЕНА ШТАТИВА (для режима ожидания)
        self._rack_replaced_btn = ttk.Button(
            buttons_frame,
            text="ШТАТИВ ЗАМЕНЁН",
            style="Resume.TButton",
            command=self._on_rack_replaced_click,
            state=tk.DISABLED,
        )
        self._rack_replaced_btn.pack(side=tk.RIGHT, padx=5)

    def _create_main_area(self, parent: tk.Frame):
        """Создание основной области: исходные штативы слева, целевые справа.

        Args:
            parent: Родительский фрейм для размещения основной области.
        """
        main_area = tk.Frame(parent, bg=COLORS["light_gray"])
        main_area.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Левая часть - исходные штативы
        left_frame = tk.LabelFrame(
            main_area,
            text="ИСХОДНЫЕ ШТАТИВЫ",
            font=FONTS["header"],
            bg=COLORS["white"],
            fg=COLORS["black"],
        )
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        self._create_pallets_section(left_frame)

        # Правая часть - целевые штативы
        right_frame = tk.LabelFrame(
            main_area,
            text="ЦЕЛЕВЫЕ ШТАТИВЫ",
            font=FONTS["header"],
            bg=COLORS["white"],
            fg=COLORS["black"],
        )
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        self._create_racks_section(right_frame)

    def _create_pallets_section(self, parent: tk.Frame):
        """Создание секции исходных штативов с вкладками.

        Args:
            parent: Родительский фрейм для размещения секции.
        """
        # Notebook для переключения между штативами
        self._pallets_notebook = ttk.Notebook(parent)
        self._pallets_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Создаём вкладки для штативов (по умолчанию 2)
        for pallet_id in [1, 2]:
            self._create_pallet_tab(pallet_id)

    def _create_pallet_tab(self, pallet_id: int):
        """Создание вкладки для одного исходного штатива.

        Каждая вкладка содержит таблицу Treeview со столбцами:
        позиция, баркод, тип анализа и статус сортировки.

        Args:
            pallet_id: Идентификатор исходного штатива.
        """
        tab_frame = tk.Frame(self._pallets_notebook, bg=COLORS["white"])
        self._pallets_notebook.add(tab_frame, text=f"Штатив {pallet_id}")

        # Таблица с пробирками
        columns = ("pos", "barcode", "type", "status")
        tree = ttk.Treeview(
            tab_frame,
            columns=columns,
            show="headings",
            style="Pallet.Treeview",
        )

        tree.heading("pos", text="Поз")
        tree.heading("barcode", text="Баркод")
        tree.heading("type", text="Тип")
        tree.heading("status", text="Статус")

        tree.column("pos", width=50, anchor=tk.CENTER)
        tree.column("barcode", width=150, anchor=tk.W)
        tree.column("type", width=100, anchor=tk.CENTER)
        tree.column("status", width=100, anchor=tk.CENTER)

        # Скроллбар
        scrollbar = ttk.Scrollbar(tab_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._pallet_trees[pallet_id] = tree

    def _create_racks_section(self, parent: tk.Frame):
        """Создание секции целевых штативов с вкладками.

        Args:
            parent: Родительский фрейм для размещения секции.
        """
        # Notebook для переключения между штативами
        self._racks_notebook = ttk.Notebook(parent, style="Racks.TNotebook")
        self._racks_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Штативы соответствуют bootstrap.py:
        # rack_id: 3=OTHER, 4-5=UGI, 6-7=VPCH, 8-9=UGI_VPCH
        rack_configs = [
            (3, TestType.OTHER),
            (4, TestType.UGI),
            (5, TestType.UGI),
            (6, TestType.VPCH),
            (7, TestType.VPCH),
            (8, TestType.UGI_VPCH),
            (9, TestType.UGI_VPCH),
        ]

        for rack_id, test_type in rack_configs:
            self._create_rack_tab(rack_id, test_type)

    def _create_rack_tab(self, rack_id: int, default_type: TestType):
        """Создание вкладки для одного целевого штатива.

        Вкладка включает: тип теста, поле ввода целевого количества,
        прогресс-бар заполнения и матрицу ячеек штатива.

        Args:
            rack_id: Идентификатор целевого штатива.
            default_type: Тип теста по умолчанию для штатива.
        """
        tab_frame = tk.Frame(self._racks_notebook, bg=COLORS["white"])
        self._racks_notebook.add(tab_frame, text=f"#{rack_id} {default_type.name}")

        # --- Верхняя панель с настройками ---
        settings_frame = tk.Frame(tab_frame, bg=COLORS["white"])
        settings_frame.pack(fill=tk.X, padx=5, pady=5)

        # Тип теста (только отображение, без редактирования)
        tk.Label(
            settings_frame,
            text=f"Тип: {default_type.name}",
            font=FONTS["small"],
            bg=COLORS["white"],
        ).pack(side=tk.LEFT)

        # Цель
        tk.Label(
            settings_frame,
            text="Цель:",
            font=FONTS["small"],
            bg=COLORS["white"],
        ).pack(side=tk.LEFT, padx=(10, 2))

        target_var = tk.StringVar(value="50")
        target_entry = tk.Entry(
            settings_frame,
            textvariable=target_var,
            width=4,
            font=FONTS["small"],
            justify=tk.CENTER,
        )
        target_entry.pack(side=tk.LEFT)
        target_entry.bind("<FocusOut>", lambda e, rid=rack_id: self._on_target_changed(rid))
        target_entry.bind("<Return>", lambda e, rid=rack_id: self._on_target_changed(rid))
        self._rack_target_entries[rack_id] = target_entry
        self._rack_target_vars[rack_id] = target_var

        # Статус заполнения
        status_label = tk.Label(
            settings_frame,
            text="0/50",
            font=FONTS["normal"],
            bg=COLORS["white"],
            fg=COLORS["dark_gray"],
        )
        status_label.pack(side=tk.LEFT, padx=10)
        self._rack_labels[rack_id] = status_label

        # Прогресс-бар
        progress = ttk.Progressbar(
            settings_frame,
            length=100,
            mode="determinate",
            maximum=50,
        )
        progress.pack(side=tk.LEFT, padx=3)
        status_label._progress = progress

        # --- Матрица ячеек ---
        matrix_frame = tk.Frame(tab_frame, bg=COLORS["white"])
        matrix_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._rack_matrix_frames[rack_id] = matrix_frame

        # Создаём матрицу с количеством ячеек = целевому значению
        self._rebuild_rack_matrix(rack_id, 50)

    def _rebuild_rack_matrix(self, rack_id: int, total_cells: int):
        """Перестроить матрицу ячеек для штатива с заданным количеством ячеек.

        Удаляет текущую матрицу и создаёт новую сетку 5 столбцов x N строк.
        Ячейки заполняются справа налево в каждой строке, чтобы соответствовать
        физическому расположению позиций в реальном штативе.

        Args:
            rack_id: Идентификатор целевого штатива.
            total_cells: Общее количество ячеек в матрице.
        """
        matrix_frame = self._rack_matrix_frames.get(rack_id)
        if not matrix_frame:
            return

        # Удаляем все существующие виджеты из фрейма матрицы
        for widget in matrix_frame.winfo_children():
            widget.destroy()

        if total_cells <= 0:
            self._rack_matrices[rack_id] = {}
            return

        # Вычисляем размеры сетки: 5 столбцов, строк столько сколько нужно
        cols = 5
        rows = (total_cells + cols - 1) // cols  # округление вверх

        # --- Создание сетки ячеек ---
        cell_labels = {}
        for row in range(rows):
            for col in range(cols):
                # Заполнение справа налево: col=0 соответствует правой позиции в строке,
                # чтобы позиция 1 оказалась в правом столбце первой строки —
                # это повторяет физическую раскладку штатива
                pos = row * cols + (cols - 1 - col) + 1
                if pos > total_cells:
                    continue

                cell_frame = tk.Frame(
                    matrix_frame,
                    bg=COLORS["light_gray"],
                    relief=tk.RIDGE,
                    bd=1,
                )
                cell_frame.grid(row=row, column=col, padx=1, pady=1, sticky="nsew")

                # Номер позиции
                pos_label = tk.Label(
                    cell_frame,
                    text=str(pos),
                    font=(FONT_FAMILY, 8),
                    bg=COLORS["light_gray"],
                    fg=COLORS["dark_gray"],
                )
                pos_label.pack(anchor=tk.NW)

                # Баркод
                barcode_label = tk.Label(
                    cell_frame,
                    text="",
                    font=(FONT_FAMILY, 7),
                    bg=COLORS["light_gray"],
                    fg=COLORS["black"],
                    anchor=tk.CENTER,
                )
                barcode_label.pack(fill=tk.X)

                # Тип анализа
                type_label = tk.Label(
                    cell_frame,
                    text="",
                    font=(FONT_FAMILY, 8),
                    bg=COLORS["light_gray"],
                    fg=COLORS["blue"],
                )
                type_label.pack()

                cell_labels[pos] = {
                    "frame": cell_frame,
                    "pos": pos_label,
                    "barcode": barcode_label,
                    "type": type_label,
                }

        # Настраиваем расширение столбцов и строк
        for col in range(cols):
            matrix_frame.columnconfigure(col, weight=1)
        for row in range(rows):
            matrix_frame.rowconfigure(row, weight=1)

        self._rack_matrices[rack_id] = cell_labels

    def _create_progress_section(self, parent: tk.Frame):
        """Создание секции прогресса сканирования и сортировки.

        Включает два прогресс-бара: количество отсканированных и
        количество отсортированных пробирок с процентами.

        Args:
            parent: Родительский фрейм для размещения секции прогресса.
        """
        progress_frame = tk.LabelFrame(
            parent,
            text="ПРОГРЕСС",
            font=FONTS["header"],
            bg=COLORS["white"],
            fg=COLORS["black"],
        )
        progress_frame.pack(fill=tk.X, pady=(0, 10))

        inner_frame = tk.Frame(progress_frame, bg=COLORS["white"])
        inner_frame.pack(fill=tk.X, padx=10, pady=10)

        # --- Сканирование ---
        scan_frame = tk.Frame(inner_frame, bg=COLORS["white"])
        scan_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            scan_frame,
            text="Отсканировано:",
            font=FONTS["normal"],
            bg=COLORS["white"],
            width=15,
            anchor=tk.W,
        ).pack(side=tk.LEFT)

        self._scan_progress = ttk.Progressbar(
            scan_frame,
            length=400,
            mode="determinate",
            style="Scan.Horizontal.TProgressbar",
        )
        self._scan_progress.pack(side=tk.LEFT, padx=5)

        self._scan_label = tk.Label(
            scan_frame,
            text="0/0",
            font=FONTS["normal"],
            bg=COLORS["white"],
            width=15,
        )
        self._scan_label.pack(side=tk.LEFT)

        # --- Сортировка ---
        sort_frame = tk.Frame(inner_frame, bg=COLORS["white"])
        sort_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            sort_frame,
            text="Отсортировано:",
            font=FONTS["normal"],
            bg=COLORS["white"],
            width=15,
            anchor=tk.W,
        ).pack(side=tk.LEFT)

        self._sort_progress = ttk.Progressbar(
            sort_frame,
            length=400,
            mode="determinate",
            style="Sort.Horizontal.TProgressbar",
        )
        self._sort_progress.pack(side=tk.LEFT, padx=5)

        self._sort_label = tk.Label(
            sort_frame,
            text="0/0",
            font=FONTS["normal"],
            bg=COLORS["white"],
            width=15,
        )
        self._sort_label.pack(side=tk.LEFT)



    # ==================== ОБРАБОТЧИКИ СОБЫТИЙ ====================

    def _on_start_click(self):
        """Обработчик кнопки СТАРТ.

        Вызывает зарегистрированный callback запуска и переводит
        приложение в статус RUNNING.

        Raises:
            Отображает messagebox при ошибке в callback.
        """
        if self._start_callback:
            try:
                self._start_callback()
                self._set_status(AppStatus.RUNNING)
                self._update_buttons_state()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось запустить: {e}")

    def _on_stop_click(self):
        """Обработчик кнопки СТОП.

        Запрашивает подтверждение у пользователя, затем вызывает
        callback остановки и переводит приложение в статус STOPPED.

        Raises:
            Отображает messagebox при ошибке в callback.
        """
        if messagebox.askyesno("Подтверждение", "Остановить программу?"):
            if self._stop_callback:
                try:
                    self._stop_callback()
                    self._set_status(AppStatus.STOPPED)
                    self._update_buttons_state()
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось остановить: {e}")

    def _on_pause_click(self):
        """Обработчик кнопки ПАУЗА.

        Вызывает callback паузы и переводит приложение в статус PAUSED.

        Raises:
            Отображает messagebox при ошибке в callback.
        """
        if self._pause_callback:
            try:
                self._pause_callback()
                self._set_status(AppStatus.PAUSED)
                self._update_buttons_state()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось приостановить: {e}")

    def _on_resume_click(self):
        """Обработчик кнопки ПРОДОЛЖИТЬ.

        Вызывает callback возобновления и переводит приложение
        в статус RUNNING.

        Raises:
            Отображает messagebox при ошибке в callback.
        """
        if self._resume_callback:
            try:
                self._resume_callback()
                self._set_status(AppStatus.RUNNING)
                self._update_buttons_state()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось возобновить: {e}")

    def _on_rack_replaced_click(self):
        """Обработчик кнопки ШТАТИВ ЗАМЕНЁН.

        Открывает полноэкранный диалог со схемой расположения штативов.
        При подтверждении замены вызывает соответствующий callback и
        переводит приложение в статус RUNNING.

        Raises:
            Отображает messagebox при ошибке в callback.
        """
        # Определяем какие rack_id нужно подсветить по причине ожидания
        highlight_rack_ids = self._get_rack_ids_from_reason(self._waiting_reason)

        dialog = RackReplacementDialog(
            self.root,
            highlight_rack_ids=highlight_rack_ids,
            reason=self._waiting_reason,
        )
        self.root.wait_window(dialog)

        if dialog.confirmed and self._rack_replaced_callback:
            try:
                self._rack_replaced_callback()
                self._set_status(AppStatus.RUNNING)
                self._update_buttons_state()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Ошибка: {e}")

    def _get_rack_ids_from_reason(self, reason: str) -> list:
        """Извлечь идентификаторы штативов из текста причины ожидания.

        Анализирует строку причины на наличие названий типов тестов
        и возвращает соответствующие rack_id.

        Args:
            reason: Текстовая причина ожидания замены штатива.

        Returns:
            Список rack_id штативов, соответствующих типу теста из причины.
        """
        # Маппинг тип теста -> rack_id
        type_to_racks = {
            "OTHER": [3],
            "UGI_VPCH": [8, 9],
            "UGI": [4, 5],
            "VPCH": [6, 7],
        }
        # Проверяем UGI_VPCH первым, т.к. он содержит подстроки UGI и VPCH,
        # которые дали бы ложное совпадение при проверке раньше
        for type_name in ["UGI_VPCH", "UGI", "VPCH", "OTHER"]:
            if type_name in reason:
                return type_to_racks[type_name]
        return []

    def _on_target_changed(self, rack_id: int):
        """Обработчик изменения целевого количества пробирок в штативе.

        Валидирует введённое значение (0-50), обновляет модель штатива,
        прогресс-бар и перестраивает матрицу ячеек.

        Args:
            rack_id: Идентификатор целевого штатива, для которого
                изменено целевое значение.
        """
        target_var = self._rack_target_vars.get(rack_id)
        if not target_var:
            return

        try:
            new_target = int(target_var.get())
            if new_target < 0:
                new_target = 0
            elif new_target > 50:
                new_target = 50

            target_var.set(str(new_target))

            if self._rack_manager:
                rack = self._rack_manager.get_destination_rack(rack_id)
                if rack:
                    rack.set_target(new_target)

            # Обновляем прогресс-бар
            label = self._rack_labels.get(rack_id)
            if label and hasattr(label, '_progress'):
                label._progress['maximum'] = new_target if new_target > 0 else 1

            # Перестраиваем матрицу ячеек под новое количество
            self._rebuild_rack_matrix(rack_id, new_target)

        except ValueError:
            # Некорректное значение - сбрасываем на 50
            target_var.set("50")

    # ==================== ОБНОВЛЕНИЕ ИНТЕРФЕЙСА ====================

    def _set_status(self, status: AppStatus):
        """Установить статус приложения и обновить отображение в статус-баре.

        Args:
            status: Новый статус приложения.
        """
        self._status = status

        # Цвет статуса
        color_map = {
            AppStatus.STOPPED: COLORS["dark_gray"],
            AppStatus.RUNNING: COLORS["green"],
            AppStatus.PAUSED: COLORS["yellow"],
            AppStatus.WAITING: COLORS["orange"],
        }

        self._status_label.configure(
            text=f"СТАТУС: {status.value}",
            fg=color_map.get(status, COLORS["black"]),
        )

    def _update_buttons_state(self):
        """Обновить доступность кнопок в зависимости от текущего статуса."""
        if self._status == AppStatus.STOPPED:
            self._start_btn.configure(state=tk.NORMAL)
            self._pause_btn.configure(state=tk.DISABLED)
            self._resume_btn.configure(state=tk.DISABLED)
            self._stop_btn.configure(state=tk.DISABLED)
            self._rack_replaced_btn.configure(state=tk.DISABLED)

        elif self._status == AppStatus.RUNNING:
            self._start_btn.configure(state=tk.DISABLED)
            self._pause_btn.configure(state=tk.NORMAL)
            self._resume_btn.configure(state=tk.DISABLED)
            self._stop_btn.configure(state=tk.NORMAL)
            self._rack_replaced_btn.configure(state=tk.DISABLED)

        elif self._status == AppStatus.PAUSED:
            self._start_btn.configure(state=tk.DISABLED)
            self._pause_btn.configure(state=tk.DISABLED)
            self._resume_btn.configure(state=tk.NORMAL)
            self._stop_btn.configure(state=tk.NORMAL)
            self._rack_replaced_btn.configure(state=tk.DISABLED)

        elif self._status == AppStatus.WAITING:
            self._start_btn.configure(state=tk.DISABLED)
            self._pause_btn.configure(state=tk.DISABLED)
            self._resume_btn.configure(state=tk.DISABLED)
            self._stop_btn.configure(state=tk.NORMAL)
            self._rack_replaced_btn.configure(state=tk.NORMAL)

    def _update_pallets_view(self):
        """Обновить отображение таблиц исходных штативов из модели данных."""
        if not self._rack_manager:
            return

        for pallet_id, tree in self._pallet_trees.items():
            pallet = self._rack_manager.get_source_pallet(pallet_id)
            if not pallet:
                continue

            # Очищаем таблицу
            for item in tree.get_children():
                tree.delete(item)

            # Добавляем пробирки
            tubes = pallet.get_tubes()
            for tube in tubes:
                status = "Отсортирована" if tube.is_placed else "Ожидает"
                tests_display = ", ".join(tube.raw_tests) if tube.raw_tests else tube.test_type.name
                tree.insert("", tk.END, values=(
                    tube.number,
                    tube.barcode,
                    tests_display,
                    status,
                ))

    def _update_racks_view(self):
        """Обновить отображение целевых штативов из модели данных.

        Обновляет счётчики заполнения, прогресс-бары, заголовки вкладок
        и содержимое матриц ячеек для каждого целевого штатива.
        """
        if not self._rack_manager:
            return

        for rack_id, label in self._rack_labels.items():
            rack = self._rack_manager.get_destination_rack(rack_id)
            if not rack:
                continue

            count = rack.get_tube_count()
            target = rack.target
            label.configure(text=f"{count}/{target}")

            # Обновляем прогресс-бар
            if hasattr(label, '_progress'):
                label._progress['maximum'] = target if target > 0 else 1
                label._progress['value'] = count

            # Обновляем поле target (только если не в фокусе)
            target_entry = self._rack_target_entries.get(rack_id)
            target_var = self._rack_target_vars.get(rack_id)
            if target_entry and target_var and self.root.focus_get() != target_entry:
                target_var.set(str(target))

            # Обновляем заголовок вкладки
            if hasattr(self, '_racks_notebook'):
                for i in range(self._racks_notebook.index("end")):
                    tab_text = self._racks_notebook.tab(i, "text")
                    if tab_text.startswith(f"#{rack_id}"):
                        self._racks_notebook.tab(i, text=f"#{rack_id} {rack.test_type.name} ({count})")

            # --- Обновление матрицы ячеек ---
            cell_labels = self._rack_matrices.get(rack_id)
            if cell_labels:
                tubes = rack.get_tubes()
                tubes_by_pos = {}
                for tube in tubes:
                    if tube.destination_number is not None:
                        # +1: destination_number в модели 0-based, а GUI-позиции 1-based
                        tubes_by_pos[tube.destination_number + 1] = tube

                # Обновляем каждую ячейку
                for pos, cell in cell_labels.items():
                    tube = tubes_by_pos.get(pos)
                    if tube:
                        cell["barcode"].configure(text=tube.barcode)
                        tests_display = ", ".join(tube.raw_tests) if tube.raw_tests else tube.test_type.name
                        cell["type"].configure(text=tests_display)
                        cell["frame"].configure(bg=COLORS["white"])
                        cell["pos"].configure(bg=COLORS["white"])
                        cell["barcode"].configure(bg=COLORS["white"])
                        cell["type"].configure(bg=COLORS["white"])
                    else:
                        cell["barcode"].configure(text="")
                        cell["type"].configure(text="")
                        cell["frame"].configure(bg=COLORS["light_gray"])
                        cell["pos"].configure(bg=COLORS["light_gray"])
                        cell["barcode"].configure(bg=COLORS["light_gray"])
                        cell["type"].configure(bg=COLORS["light_gray"])

    def _update_progress(self):
        """Обновить прогресс-бары сканирования и сортировки.

        Подсчитывает суммарную статистику по всем исходным штативам
        и обновляет прогресс-бары с процентами.
        """
        if not self._rack_manager:
            return

        # --- Подсчёт статистики ---
        total_scanned = 0
        total_sorted = 0
        max_tubes = 0

        for pallet in self._rack_manager.get_all_source_pallets():
            total_scanned += pallet.get_tube_count()
            total_sorted += pallet.get_sorted_count()
            max_tubes += pallet.MAX_TUBES

        # --- Сканирование ---
        self._scan_progress['maximum'] = max_tubes if max_tubes > 0 else 1
        self._scan_progress['value'] = total_scanned
        percent_scan = int(total_scanned / max_tubes * 100) if max_tubes > 0 else 0
        self._scan_label.configure(text=f"{total_scanned}/{max_tubes} ({percent_scan}%)")

        # --- Сортировка ---
        self._sort_progress['maximum'] = total_scanned if total_scanned > 0 else 1
        self._sort_progress['value'] = total_sorted
        percent_sort = int(total_sorted / total_scanned * 100) if total_scanned > 0 else 0
        self._sort_label.configure(text=f"{total_sorted}/{total_scanned} ({percent_sort}%)")

    def _schedule_updates(self):
        """Запланировать периодическое обновление отображения штативов.

        Запускает цикл обновления каждые 500 мс через ``root.after``.
        """
        def update():
            self._update_pallets_view()
            self._update_racks_view()
            self.root.after(500, update)  # Каждые 500мс

        update()

    # ==================== ПУБЛИЧНЫЙ ИНТЕРФЕЙС ====================

    def set_rack_manager(self, rack_manager: RackSystemManager):
        """Установить менеджер штативов для отображения данных.

        Args:
            rack_manager: Экземпляр менеджера системы штативов.
        """
        self._rack_manager = rack_manager

    def set_start_callback(self, callback: Callable):
        """Установить callback для кнопки запуска.

        Args:
            callback: Функция, вызываемая при нажатии кнопки СТАРТ.
        """
        self._start_callback = callback

    def set_stop_callback(self, callback: Callable):
        """Установить callback для кнопки остановки.

        Args:
            callback: Функция, вызываемая при нажатии кнопки СТОП.
        """
        self._stop_callback = callback

    def set_pause_callback(self, callback: Callable):
        """Установить callback для кнопки паузы.

        Args:
            callback: Функция, вызываемая при нажатии кнопки ПАУЗА.
        """
        self._pause_callback = callback

    def set_resume_callback(self, callback: Callable):
        """Установить callback для кнопки продолжения.

        Args:
            callback: Функция, вызываемая при нажатии кнопки ПРОДОЛЖИТЬ.
        """
        self._resume_callback = callback

    def set_rack_replaced_callback(self, callback: Callable):
        """Установить callback для подтверждения замены штатива.

        Args:
            callback: Функция, вызываемая после подтверждения замены
                штатива через диалог.
        """
        self._rack_replaced_callback = callback

    def set_waiting_mode(self, reason: str):
        """Перевести приложение в режим ожидания замены штатива.

        Args:
            reason: Причина ожидания, отображается в диалоге замены.
        """
        self._waiting_reason = reason
        self._set_status(AppStatus.WAITING)
        self._update_buttons_state()

    def exit_waiting_mode(self):
        """Выйти из режима ожидания и вернуться в статус RUNNING."""
        self._set_status(AppStatus.RUNNING)
        self._update_buttons_state()

    def update_lis_stats(self, in_queue: int, received: int, ready_to_sort: int):
        """Обновить статистику ЛИС (убрано из интерфейса).

        Args:
            in_queue: Количество пробирок в очереди.
            received: Количество полученных результатов.
            ready_to_sort: Количество пробирок, готовых к сортировке.
        """
        pass

    def run(self):
        """Запустить главный цикл обработки событий Tkinter."""
        self.root.mainloop()


# ==================== ТОЧКА ВХОДА ДЛЯ ТЕСТИРОВАНИЯ ====================

def main():
    """Запуск GUI с тестовыми данными для отладки интерфейса."""
    root = tk.Tk()
    app = SorterGUI(root)

    # Тестовые данные
    from src.eppendorf_sorter.domain.racks import (
        RackSystemManager,
        SourceRack,
        DestinationRack,
        TestType,
        TubeInfo,
    )

    # Создаём менеджер
    manager = RackSystemManager()

    # Исходные штативы
    pallet1 = SourceRack(1)
    pallet2 = SourceRack(2)

    # Добавляем тестовые пробирки
    for i in range(10):
        tube = TubeInfo(
            barcode=f"270120091{i}",
            source_rack=1,
            number=i,
            test_type=[TestType.UGI, TestType.VPCH, TestType.UGI_VPCH, TestType.OTHER][i % 4],
        )
        pallet1.add_scanned_tube(tube)

    manager.add_source_pallet(pallet1)
    manager.add_source_pallet(pallet2)

    # Целевые штативы (destination)
    rack_types = [
        TestType.UGI, TestType.UGI,
        TestType.VPCH, TestType.VPCH,
        TestType.UGI_VPCH, TestType.UGI_VPCH,
        TestType.OTHER, TestType.OTHER,
    ]

    for i, test_type in enumerate(rack_types, start=1):
        rack = DestinationRack(i, test_type)
        manager.add_destination_rack(rack)

    app.set_rack_manager(manager)

    # Тестовые callbacks
    app.set_start_callback(lambda: print("START"))
    app.set_stop_callback(lambda: print("STOP"))
    app.set_pause_callback(lambda: print("PAUSE"))
    app.set_resume_callback(lambda: print("RESUME"))

    app.run()


if __name__ == "__main__":
    main()
