"""Three-section Windows workspace. All device I/O runs outside Tk's event loop."""

import ctypes
import os
import queue
import threading
import tkinter as tk
import winreg
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import backend
import diagnostics
import dump_core
from version import VERSION

PAGES = ("Прошивка", "Диагностика", "Дамп устройства")
MODELS = {0x436: "One Pro", 0x438: "One", 0x43E: "1S"}


def system_colors():
    class HighContrast(ctypes.Structure):
        _fields_ = [
            ("size", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("scheme", ctypes.c_void_p),
        ]

    settings = HighContrast()
    settings.size = ctypes.sizeof(settings)
    ctypes.windll.user32.SystemParametersInfoW(
        0x42, settings.size, ctypes.byref(settings), 0
    )
    if settings.flags & 1:
        return "contrast"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return (
                "light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "dark"
            )
    except OSError:
        return "light"


class Workspace:
    def __init__(self, root, run_operation):
        self.root, self.run_operation = root, run_operation
        self.scale = root.winfo_fpixels("1i") / 96
        self.page = tk.StringVar(value=PAGES[0])
        self.theme = tk.StringVar(value="system")
        self.notice = tk.StringVar()
        self.last_operation = None
        self.file_status = tk.StringVar()
        self.file = self.firmware = self.build = self.device = None
        self.folder = self.report = None
        self.result = ""
        self.dump_status = ""
        self.ready = False
        self.activity = ""
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.devices = []
        self.nav_buttons = []
        self.wrapping = []
        self.style = ttk.Style(root)
        root.title("XREAL Updater · Windows Preview")
        size = lambda n: round(n * self.scale)
        root.geometry(
            f"{min(size(850), root.winfo_screenwidth() - 32)}x{min(size(550), root.winfo_screenheight() - 80)}"
        )
        root.minsize(
            min(size(390), root.winfo_screenwidth() - 32),
            min(size(340), root.winfo_screenheight() - 80),
        )
        root.protocol("WM_DELETE_WINDOW", self.close)
        bar = tk.Menu(root)
        menu = tk.Menu(bar, tearoff=False)
        for name, value in [
            ("Системная", "system"),
            ("Светлая", "light"),
            ("Тёмная", "dark"),
        ]:
            menu.add_radiobutton(
                label=name, variable=self.theme, value=value, command=self.colors
            )
        bar.add_cascade(label="Вид", menu=menu)
        root.config(menu=bar)
        for index, page in enumerate(PAGES, 1):
            root.bind(
                f"<Control-Key-{index}>", lambda _, target=page: self.navigate(target)
            )
        self.nav = ttk.Frame(root, style="Nav.TFrame", padding=size(12))
        ttk.Label(self.nav, text="XREAL Updater", style="Brand.TLabel").pack(
            anchor="w", pady=(6, size(20))
        )
        for glyph, page in zip(("↓", "∿", "▤"), PAGES):
            b = ttk.Radiobutton(
                self.nav,
                text=glyph + "   " + page,
                variable=self.page,
                value=page,
                command=self.render,
                style="Nav.TRadiobutton",
            )
            b.pack(fill="x", pady=2)
            self.nav_buttons.append(b)
        ttk.Label(
            self.nav,
            text="Версия " + VERSION,
            style="Brand.TLabel",
            font=("Segoe UI", 9),
        ).pack(side="bottom", anchor="w")
        self.selector = ttk.Combobox(
            root, values=PAGES, textvariable=self.page, state="readonly"
        )
        self.selector.bind("<<ComboboxSelected>>", lambda _: self.render())
        self.main = ttk.Frame(root)
        self.main.grid(row=1, column=1, sticky="nsew")
        root.rowconfigure(1, weight=1)
        root.columnconfigure(1, weight=1)
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.main, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll = ttk.Scrollbar(self.main, command=self.canvas.yview)
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.body = ttk.Frame(self.canvas, padding=size(18))
        self.win = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.foot = ttk.Frame(self.main, padding=(size(16), size(8)))
        self.foot.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Separator(self.main).grid(row=1, column=0, columnspan=2, sticky="ew")
        self.body.bind("<Configure>", self.scroll_region)
        self.canvas.bind("<Configure>", self.resize_body)
        root.bind("<Configure>", self.resize)
        root.bind("<FocusIn>", self.reveal)
        root.bind("<MouseWheel>", self.wheel)
        self.colors()
        self.refresh()
        self.resize()
        self.timer = root.after(60, self.poll)

    def navigate(self, page):
        if not self.activity:
            self.page.set(page)
            self.render()
            self.root.update_idletasks()
            target = (
                self.combo
                if self.combo.winfo_ismapped()
                else self.selector
                if self.selector.winfo_ismapped()
                else self.nav_buttons[PAGES.index(page)]
            )
            target.focus_set()

    def colors(self):
        mode = system_colors()
        if mode != "contrast" and self.theme.get() != "system":
            mode = self.theme.get()
        if mode == "contrast":
            bg, nav, fg, mut, accent, white, selected = (
                "SystemWindow",
                "SystemButtonFace",
                "SystemWindowText",
                "SystemWindowText",
                "SystemHighlight",
                "SystemHighlightText",
                "SystemButtonFace",
            )
        elif mode == "dark":
            bg, nav, fg, mut, accent, white, selected = (
                "#25282A",
                "#1C1F21",
                "#F3F3F0",
                "#C2C9CB",
                "#006B77",
                "#FFFFFF",
                "#314E53",
            )
        else:
            bg, nav, fg, mut, accent, white, selected = (
                "#FAF9F6",
                "#F0F1EF",
                "#202628",
                "#505B5D",
                "#006B77",
                "#FFFFFF",
                "#D7EAEC",
            )
        self.bg, self.fg = bg, fg
        s = self.style
        s.theme_use("clam")
        s.configure(".", font=("Segoe UI", 10), background=bg, foreground=fg)
        s.configure("TButton", padding=(8, 5), background=nav, focuscolor=fg)
        s.map(
            "TButton", background=[("active", selected)], foreground=[("disabled", mut)]
        )
        s.configure(
            "Primary.TButton", background=accent, foreground=white, focuscolor=white
        )
        s.map(
            "Primary.TButton",
            background=[("disabled", nav), ("active", accent)],
            foreground=[("disabled", mut), ("!disabled", white)],
        )
        s.configure("Nav.TFrame", background=nav)
        s.configure("Brand.TLabel", background=nav, font=("Segoe UI", 13, "bold"))
        s.layout("Nav.TRadiobutton", s.layout("Toolbutton"))
        s.configure(
            "Nav.TRadiobutton",
            background=nav,
            padding=(8, 8),
            focuscolor=fg,
            anchor="w",
        )
        s.map(
            "Nav.TRadiobutton",
            background=[("selected", selected), ("active", selected)],
        )
        s.configure("TCombobox", fieldbackground=bg, foreground=fg, arrowcolor=fg)
        s.map(
            "TCombobox",
            fieldbackground=[("readonly", bg)],
            foreground=[("readonly", fg)],
        )
        self.root.option_add("*TCombobox*Listbox.background", bg)
        self.root.option_add("*TCombobox*Listbox.foreground", fg)
        s.configure("Muted.TLabel", foreground=mut)
        s.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        s.configure("Heading.TLabel", font=("Segoe UI", 12, "bold"))
        self.canvas.configure(bg=bg)
        if hasattr(self, "report_box") and self.report_box.winfo_exists():
            self.report_box.configure(bg=bg, fg=fg)

    def resize(self, event=None):
        if event and event.widget != self.root:
            return
        if self.root.winfo_width() < 710 * self.scale:
            self.nav.grid_remove()
            self.selector.grid(row=0, column=1, sticky="ew", padx=10, pady=5)
        else:
            self.selector.grid_remove()
            self.nav.grid(row=0, column=0, rowspan=2, sticky="ns")
        if hasattr(self, "status_label") and self.status_label.winfo_exists():
            self.status_label.configure(
                wraplength=max(140, self.main.winfo_width() - 42 * self.scale)
            )

    def resize_body(self, event):
        self.canvas.itemconfigure(self.win, width=event.width)
        if hasattr(self, "status_label"):
            self.status_label.configure(
                wraplength=max(150, event.width - 42 * self.scale)
            )
        for w in self.wrapping:
            w.configure(wraplength=max(100, event.width - 45 * self.scale))

    def scroll_region(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox(self.win))
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.scroll.grid()
        else:
            self.scroll.grid_remove()

    def reveal(self, event):
        w = event.widget
        if not str(w).startswith(str(self.body) + "."):
            return
        y = w.winfo_rooty() - self.body.winfo_rooty()
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        if y < top or y + w.winfo_height() > top + height:
            self.canvas.yview_moveto(max(0, y - 8) / max(1, self.body.winfo_height()))

    def wheel(self, event):
        if event.widget.winfo_toplevel() == self.root and not isinstance(
            event.widget, (tk.Text, ttk.Combobox)
        ):
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def label(self, text="", style="TLabel", variable=None):
        w = ttk.Label(
            self.body,
            text=text,
            textvariable=variable,
            style=style,
            justify="left",
            wraplength=max(150, self.canvas.winfo_width() - 45 * self.scale),
        )
        w.pack(anchor="w", fill="x", pady=3)
        self.wrapping.append(w)
        return w

    def button(self, parent, text, command, enabled=True, style="TButton"):
        b = ttk.Button(
            parent,
            text=text,
            command=command,
            state="normal" if enabled else "disabled",
            style=style,
        )
        b.pack(side="left", padx=(0, 4))
        return b

    def render(self):
        for parent in (self.body, self.foot):
            for w in parent.winfo_children():
                w.destroy()
        self.wrapping.clear()
        self.canvas.yview_moveto(0)
        page = self.page.get()
        free = not self.activity
        self.label("Установка прошивки" if page == PAGES[0] else page, "Title.TLabel")
        self.label(
            [
                "Выберите очки и пакет обновления Pilot.",
                "Проверка USB, версий и совместимости.",
                "Сохраните доступные файлы с очков в выбранную папку.",
            ][PAGES.index(page)],
            "Muted.TLabel",
        )
        ttk.Separator(self.body).pack(fill="x", pady=10)
        self.label("Устройство", "Heading.TLabel")
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=4)
        self.combo = ttk.Combobox(
            row,
            state="readonly" if free else "disabled",
            width=10,
            values=[self.device_name(d, i) for i, d in enumerate(self.devices)],
        )
        if self.devices:
            self.combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        else:
            ttk.Label(row, text="Очки не найдены").pack(
                side="left", fill="x", expand=True
            )
        if self.device in self.devices:
            self.combo.current(self.devices.index(self.device))
        else:
            self.combo.set("Выберите очки" if self.devices else "Очки не найдены")
        self.combo.bind("<<ComboboxSelected>>", self.select)
        self.button(row, "Обновить", self.refresh, free)
        if not self.devices:
            self.label("Подключите USB-кабель с передачей данных.", "Muted.TLabel")
        if page == PAGES[0]:
            ttk.Separator(self.body).pack(fill="x", pady=10)
            self.label("Файл прошивки", "Heading.TLabel")
            row = ttk.Frame(self.body)
            row.pack(fill="x", pady=5)
            self.button(
                row, "Заменить BIN…" if self.file else "Выбрать BIN…", self.choose, free
            ).pack_configure(side="right")
            self.filename_label = ttk.Label(
                row,
                text=self.file.name if self.file else "BIN не выбран",
                justify="left",
            )
            self.filename_label.pack(side="left", fill="x", expand=True)
            self.filename_label.bind(
                "<Configure>",
                lambda e: e.widget.configure(wraplength=max(40, e.width - 4)),
            )
            self.label(style="Muted.TLabel", variable=self.file_status)
        elif page == PAGES[1]:
            self.report_box = tk.Text(
                self.body,
                height=8,
                width=20,
                wrap="word",
                bg=self.bg,
                fg=self.fg,
                font=("Consolas", 10),
            )
            self.report_box.pack(fill="x", pady=8)
            self.report_box.insert(
                "end",
                diagnostics.render(self.report)
                if self.report
                else "Проверка ещё не запускалась.",
            )
            self.report_box.configure(state="disabled")
            self.report_box.bind(
                "<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1]
            )
            row = ttk.Frame(self.body)
            row.pack(anchor="w")
            self.button(row, "Скопировать", self.copy, bool(self.report))
            self.button(row, "Сохранить TXT…", self.save, bool(self.report))
        else:
            self.label(
                "Сохраняются ответы USB, распакованные файлы и report.json. Полнота зависит от модели. Проверьте содержимое перед отправкой.",
                "Muted.TLabel",
            )
            if self.folder:
                self.label(str(self.folder))
                row = ttk.Frame(self.body)
                row.pack(anchor="w", pady=6)
                self.button(
                    row, "Открыть папку", self.open_folder, self.folder.is_dir()
                )
        self.status_label = ttk.Label(
            self.foot,
            textvariable=self.notice,
            wraplength=max(150, self.main.winfo_width() - 42 * self.scale),
        )
        self.status_label.pack(fill="x", pady=(0, 5))
        if self.activity:
            self.progress = ttk.Progressbar(self.foot, maximum=100)
            self.progress.pack(fill="x", pady=(0, 4))
        actions = ttk.Frame(self.foot)
        actions.pack(fill="x")
        if page == PAGES[0]:
            self.button(
                actions,
                "Проверить без записи",
                lambda: self.operate(True),
                free and self.ready,
            )
            self.install = self.button(
                actions,
                "Установить прошивку",
                lambda: self.operate(False),
                free and self.ready,
                "Primary.TButton",
            )
            self.install.pack_configure(side="right")
        elif page == PAGES[1]:
            self.button(
                actions, "Проверить сейчас", self.diagnose, free, "Primary.TButton"
            )
        else:
            self.button(
                actions,
                "Сохранить дамп…",
                self.dump,
                free and bool(self.device),
                "Primary.TButton",
            )
            if self.activity == "dump":
                self.button(actions, "Остановить", self.cancel)
        self.selector.configure(state="readonly" if free else "disabled")
        for b in self.nav_buttons:
            b.configure(state="normal" if free else "disabled")
        if free:
            if page == PAGES[2]:
                self.notice.set(
                    self.dump_status
                    or (
                        "Выберите папку для результата."
                        if self.device
                        else "Подключите и выберите очки."
                    )
                )
            elif page == PAGES[1]:
                self.notice.set(
                    (
                        "Неизвестный SKU. Откройте «Дамп устройства»."
                        if self.device
                        and self.device["product_id"] == 0x436
                        and self.report.get("sku")
                        and self.report["sku"] not in backend.SUPPORTED_SKUS
                        else "Отчёт готов."
                    )
                    if self.report
                    else "Можно проверить подключение без BIN."
                )
            else:
                self.notice.set(
                    self.result
                    or (
                        "Выберите BIN."
                        if self.device
                        else "Подключите и выберите очки."
                        if self.file
                        else "Для установки подключите очки и выберите BIN."
                    )
                )

    @staticmethod
    def device_name(d, i):
        model = MODELS.get(d["product_id"], f"PID {d['product_id']:04X}")
        return f"XREAL {model} · устройство {i + 1}"

    def refresh(self):
        if self.activity:
            return
        self.result = ""
        self.ready = False
        try:
            self.devices = dump_core.devices()
        except (OSError, RuntimeError, ValueError) as exc:
            self.devices = []
            self.device = None
            self.result = "Ошибка USB. Откройте диагностику."
            self.report = diagnostics.report("diagnostic", "usb", error=exc)
            self.render()
            return
        self.device = self.device if self.device in self.devices else None
        if len(self.devices) == 1:
            self.device = self.devices[0]
        self.preflight()

    def select(self, _):
        self.device = self.devices[self.combo.current()]
        self.preflight()

    def preflight(self):
        self.ready = False
        self.result = ""
        if self.device and self.device["product_id"] not in MODELS:
            self.result = "Неизвестное устройство. Откройте «Дамп устройства»."
        elif self.device and self.firmware:
            self.task(
                "compat", "Проверка совместимости с выбранными очками…", self.collect
            )
            return
        self.render()

    def choose(self):
        path = filedialog.askopenfilename(
            parent=self.root, filetypes=[("Прошивка XREAL", "*.bin")]
        )
        if path:
            self.file = Path(path)
            self.firmware = self.build = None
            self.ready = False
            self.file_status.set("")
            self.task(
                "bin",
                "Проверка содержимого BIN…",
                lambda: backend.identify_package(path, return_manifest=True),
            )

    def collect(self):
        return diagnostics.collect(
            self.device,
            self.file if self.firmware else None,
            enumerate_devices=dump_core.devices,
            identity_reader=dump_core.Reader,
        )

    def diagnose(self):
        self.task("diag", "Чтение идентификаторов и версий…", self.collect)

    def operate(self, verify):
        if self.activity or not self.ready:
            return
        if not verify and not messagebox.askokcancel(
            "Установить прошивку?",
            self.combo.get()
            + "\n"
            + self.file.name
            + "\n\nОставьте USB-кабель подключённым до завершения проверки.",
            parent=self.root,
            default="cancel",
        ):
            return
        self.task(
            "verify" if verify else "install",
            "Подготовка…",
            lambda: self.run_operation(
                self.device, self.firmware, self.file, verify, self.build, self.events
            ),
        )

    def copy(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(diagnostics.render(self.report))

    def save(self):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".txt",
            initialfile="XREAL-diagnostic.txt",
        )
        if path:
            try:
                Path(path).write_text(diagnostics.render(self.report), encoding="utf8")
            except OSError as exc:
                messagebox.showerror("Сохранение", str(exc), parent=self.root)

    def dump(self):
        folder = filedialog.askdirectory(parent=self.root, title="Куда сохранить дамп?")
        if not folder:
            return
        self.folder = Path(folder) / datetime.now().strftime(
            "XREAL-dump-%Y%m%d-%H%M%S-%f"
        )
        self.stop.clear()
        self.task(
            "dump",
            "Подготовка к чтению…",
            lambda: dump_core.capture(
                self.device,
                self.folder,
                lambda msg: self.events.put(("progress", msg, 0)),
                self.stop,
            ),
        )

    def cancel(self):
        self.stop.set()
        self.notice.set("Остановка после текущего чтения…")

    def open_folder(self):
        try:
            os.startfile(self.folder)
        except OSError as exc:
            messagebox.showerror("Проводник", str(exc), parent=self.root)

    def task(self, kind, message, fn):
        if self.activity:
            return
        self.activity = kind
        self.notice.set(message)
        self.render()

        def work():
            try:
                self.events.put(("finished", kind, fn()))
            except Exception as exc:  # noqa: BLE001 - surface worker failures and unlock the UI
                self.events.put(("failed", kind, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            event, kind, value = self.events.get_nowait()
            if event == "progress":
                if not (self.activity == "dump" and self.stop.is_set()):
                    self.notice.set(kind)
                self.progress.configure(value=value)
            elif event == "support":
                self.report = self.last_operation = kind
            elif event == "error":
                self.result = "Операция не завершена. Откройте диагностику."
            elif event == "finished" or event == "failed":
                self.activity = ""
                if event == "failed":
                    self.result = f"Ошибка: {value}"
                    if kind == "bin":
                        self.file_status.set(self.result)
                        self.report = diagnostics.report(
                            "bin", "validation", error=value
                        )
                    if kind == "dump":
                        self.dump_status = self.result
                elif kind == "bin":
                    self.firmware, self.build = value
                    self.file_status.set("Содержимое BIN проверено.")
                    self.preflight()
                    continue
                elif kind == "diag":
                    self.report = (
                        {"last_operation": self.last_operation, "diagnostic": value}
                        if self.last_operation
                        else value
                    )
                    if self.firmware:
                        self.ready = bool(
                            value.get("ok")
                            and self.device
                            and self.device["product_id"] in MODELS
                        )
                        self.result = (
                            "Пакет подходит выбранным очкам."
                            if self.ready
                            else "Проверка не пройдена. Откройте диагностику."
                        )
                elif kind == "compat":
                    self.report = value
                    self.ready = value["ok"]
                    self.result = (
                        "Пакет подходит выбранным очкам."
                        if self.ready
                        else "Несовместимость. Подробности в диагностике; для исследования откройте «Дамп устройства»."
                    )
                elif kind == "dump":
                    states = {
                        "finished": "Чтение завершено.",
                        "cancelled": "Чтение остановлено. Частичный результат сохранён.",
                        "interrupted": "Чтение прервано.",
                        "limit": "Достигнут предел чтения.",
                    }
                    self.dump_status = (
                        states.get(value["state"], value["state"])
                        + " "
                        + value.get("error", "")
                    )
                else:
                    self.ready = False
                    if self.report and self.report.get("ok"):
                        self.result = "Установленные файлы сверены."
                self.render()
        self.timer = self.root.after(60, self.poll)

    def close(self):
        if self.activity:
            messagebox.showinfo(
                "Операция выполняется",
                "Дождитесь завершения текущей операции.",
                parent=self.root,
            )
        else:
            self.root.after_cancel(self.timer)
            self.root.destroy()
