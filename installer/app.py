"""Standalone Windows GUI. Merely opening this app never starts a write."""
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import backend


class App:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.events = queue.Queue()
        self.found = []
        root.title('XREAL One / One Pro · Русское меню · v0.6.0-test')
        scale = float(root.tk.call('tk', 'scaling')) / (96 / 72)
        left, top, right, bottom = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
        if sys.platform == 'win32':
            from ctypes.wintypes import RECT
            area = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0):
                left, top, right, bottom = area.left, area.top, area.right, area.bottom
        width = min(round(720 * scale), max(200, right-left-40))
        height = min(round(640 * scale), max(180, bottom-top-80))
        root.geometry(f'{width}x{height}+{left+20}+{top+20}')
        root.minsize(min(320, width), min(220, height))
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style()
        style.theme_use('vista' if 'vista' in style.theme_names() else 'clam')
        style.configure('TLabel', font=('Segoe UI', 11))
        style.configure('TButton', font=('Segoe UI', 11), padding=8)
        shell = ttk.Frame(root)
        shell.pack(fill='both', expand=True)
        shell.rowconfigure(0, weight=1)
        shell.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(shell, highlightthickness=0, yscrollincrement=20,
                                background=style.lookup('TFrame', 'background') or '#f0f0f0')
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.vscroll = ttk.Scrollbar(shell, orient='vertical', command=self.canvas.yview)
        self.vscroll.grid(row=0, column=1, sticky='ns')
        self.hscroll = ttk.Scrollbar(shell, orient='horizontal', command=self.canvas.xview)
        self.hscroll.grid(row=1, column=0, sticky='ew')
        self.canvas.configure(yscrollcommand=self.vscroll.set, xscrollcommand=self.hscroll.set)
        frame = ttk.Frame(self.canvas, padding=20)
        self.content = frame
        self.content_window = self.canvas.create_window(0, 0, window=frame, anchor='nw')
        ttk.Label(frame, text='Русское меню для XREAL One / One Pro', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Подключите очки по USB → выберите устройство и прошивку → установите.',
                  wraplength=650).pack(anchor='w', pady=(10, 18))
        ttk.Label(frame, text='1. Выберите очки').pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(8, 16))
        self.combo = ttk.Combobox(row, state='readonly', font=('Segoe UI', 11))
        self.combo.pack(fill='x')
        self.combo.bind('<<ComboboxSelected>>', self.selected)
        self.refresh_button = ttk.Button(row, text='Обновить список', command=self.refresh)
        self.refresh_button.pack(anchor='w', pady=(6, 0))
        ttk.Label(frame, text='2. Выберите BIN-файл прошивки').pack(anchor='w', pady=(2, 8))
        self.firmware = None
        self.firmware_path = None
        self.file_name = tk.StringVar(value='Файл не выбран')
        ttk.Entry(frame, textvariable=self.file_name, state='readonly').pack(fill='x')
        self.browse_button = ttk.Button(frame, text='Выбрать BIN…', command=self.choose_firmware)
        self.browse_button.pack(anchor='w', pady=(6, 8))
        self.description = ttk.Label(frame, wraplength=640, justify='left')
        self.description.pack(anchor='w', pady=(5, 10))
        ttk.Label(frame, text='EXE и BIN скачиваются отдельно. Приложение проверяет формат, контрольные суммы '
                  'и совместимость BIN перед записью. Русский заменяет польский язык, английский сохраняется.',
                  wraplength=640).pack(anchor='w')
        ttk.Label(frame, text='Закройте страницу обновления XREAL. Не отключайте USB-кабель во время записи.',
                  foreground='#6b4c16', wraplength=640).pack(anchor='w', pady=(10, 14))
        self.install_button = ttk.Button(frame, text='3. Установить выбранную прошивку', command=self.start, state='disabled')
        self.install_button.pack(anchor='w')
        self.verify_button = ttk.Button(frame, text='Проверить без записи', command=lambda: self.start(verify_only=True), state='disabled')
        self.verify_button.pack(anchor='w', pady=(6, 0))
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill='x', pady=(18, 10))
        self.status = tk.StringVar(value='Поиск подключённых очков…')
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=640)
        self.status_label.pack(anchor='w')
        self.labels = [child for child in frame.winfo_children() if isinstance(child, ttk.Label)]
        self.firmware_changed()
        self.canvas.bind('<Configure>', self.layout)
        frame.bind('<Configure>', self.content_changed)
        root.bind('<MouseWheel>', self.mousewheel)
        root.bind('<FocusIn>', self.reveal_focus)
        root.after(100, self.refresh)
        root.after(100, self.poll)

    def layout(self, _event=None):
        available = max(1, self.canvas.winfo_width())
        # On unusually narrow/high-DPI displays keep every control reachable
        # through horizontal scrolling instead of clipping a button.
        minimum = max(button.winfo_reqwidth() for button in
                      (self.install_button, self.verify_button, self.refresh_button, self.browse_button)) + 40
        width = max(available, minimum)
        self.canvas.itemconfigure(self.content_window, width=width)
        for label in self.labels:
            label.configure(wraplength=max(80, width-40))
        if width > available:
            self.hscroll.grid()
        else:
            self.hscroll.grid_remove()
            self.canvas.xview_moveto(0)

    def content_changed(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox(self.content_window))

    def mousewheel(self, event):
        if self.content.winfo_height() > self.canvas.winfo_height() and event.delta:
            units = max(1, abs(event.delta)//120)
            self.canvas.yview_scroll(-units if event.delta > 0 else units, 'units')
        return 'break'

    def reveal_focus(self, event):
        widget = event.widget
        if widget == self.root or not str(widget).startswith(str(self.content)+'.'):
            return
        self.root.update_idletasks()
        y = widget.winfo_rooty()-self.content.winfo_rooty()
        visible_top = self.canvas.canvasy(0)
        visible_height = self.canvas.winfo_height()
        if y < visible_top:
            self.canvas.yview_moveto(max(0, y-8)/max(1, self.content.winfo_height()))
        elif y+widget.winfo_height() > visible_top+visible_height:
            self.canvas.yview_moveto((y+widget.winfo_height()+8-visible_height)/max(1, self.content.winfo_height()))
            self.canvas.yview_scroll(1, 'units')

    def selected(self, _event=None):
        enabled = self.combo.current() >= 0 and self.firmware is not None and not self.busy
        self.install_button.configure(state='normal' if enabled else 'disabled')
        self.verify_button.configure(state='normal' if enabled else 'disabled')

    def firmware_changed(self):
        if self.firmware is backend.STANDARD:
            self.description.configure(text='Русская Pilot 1.9.1 для XREAL One и One Pro. '
                                       'Официальный пакет Pilot 1.9.1 для обеих моделей имеет один MD5. '
                                       'Переведено 437 строк, шрифт согласован. На One перевод пока не проверен.')
        elif self.firmware is backend.ONE_EXPERIMENTAL:
            self.description.configure(text='Тестовая Pilot 2.0 только для обычных XREAL One. '
                                       'Русское меню, AI HDR и Real3D 2D→3D с CPU-обработкой. '
                                       'Настройки панели One, без профиля яркости Hebe и разгона. '
                                       'На обычном One работа этих функций ещё не проверена; '
                                       'плавность и глубина требуют проверки владельцем.')
        elif self.firmware is backend.EXPERIMENTAL:
            self.description.configure(text='Только для One Pro X1112010. Русское меню, AI HDR, '
                                       'режимы «Чтение» и «Кино», профиль Hebe с luminance до 500 '
                                       'и экспериментальный Real3D с CPU-обработкой. На одном устройстве '
                                       'глубина заметна слабо; измерено около 53–55 обработанных кадров/с. '
                                       'Стабильные 60 кадров/с не подтверждены.')
        else:
            self.description.configure(text='Выберите BIN-файл подходящей версии Pilot.')
        self.selected()

    def choose_firmware(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title='Выберите русскую прошивку',
                                          filetypes=[('Прошивка XREAL', '*.bin')])
        if not path:
            return
        self.firmware = None
        self.firmware_path = None
        self.selected()
        self.browse_button.configure(state='disabled')
        self.file_name.set(Path(path).name)
        self.status.set('Проверка BIN и контрольных сумм…')
        threading.Thread(target=self.check_firmware, args=(path,), daemon=True).start()

    def check_firmware(self, path):
        try:
            firmware = backend.identify_package(path)
            self.events.put(('file_ok', path, firmware))
        except Exception as error:
            self.events.put(('file_error', str(error), 0))

    def refresh(self):
        if self.busy:
            return
        try:
            self.found = backend.devices()
            self.combo['values'] = [f"XREAL {'One' if d.get('product_id') == 0x0438 else 'One Pro'} — устройство {i+1}" +
                                    (f" · {d['serial_number'][-6:]}" if d.get('serial_number') else '')
                                    for i, d in enumerate(self.found)]
            self.combo.set('')
            self.install_button.configure(state='disabled')
            self.verify_button.configure(state='disabled')
            self.status.set('Выберите очки в списке.' if self.found else
                            'Очки не найдены. Подключите кабель с передачей данных и нажмите «Обновить список».')
        except Exception as e:
            self.status.set('Не удалось получить список USB-устройств: '+str(e))

    def start(self, verify_only=False):
        index = self.combo.current()
        if self.busy or index < 0:
            return
        selected = self.found[index].copy()
        self.busy = True
        firmware = self.firmware
        path = self.firmware_path
        for widget in [self.install_button, self.verify_button, self.refresh_button,
                       self.combo, self.browse_button]:
            widget.configure(state='disabled')
        self.progress['value'] = 0
        self.status.set('Подготовка…')
        threading.Thread(target=self.worker, args=(selected, firmware, path, verify_only), daemon=False).start()

    def worker(self, selected, firmware, path, verify_only=False):
        # Hold the machine awake during transmission and read-back, on this same thread.
        try:
            if sys.platform == 'win32':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            action = backend.verify_only if verify_only else backend.update
            action(selected, lambda text, percent: self.events.put(('progress', text, percent)),
                   firmware=firmware, path=path)
            self.events.put(('done', '', 100))
        except Exception as e:
            log = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'XREAL-Russian-Updater' / 'last-error.txt'
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(traceback.format_exc(), encoding='utf-8')
            except OSError:
                pass
            self.events.put(('error', str(e), 0))
        finally:
            if sys.platform == 'win32':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

    def poll(self):
        try:
            while True:
                kind, text, percent = self.events.get_nowait()
                if kind == 'file_ok':
                    self.firmware = percent
                    self.firmware_path = Path(text)
                    self.browse_button.configure(state='normal')
                    self.status.set('BIN проверен. Выберите очки и запустите установку.')
                    self.firmware_changed()
                elif kind == 'file_error':
                    self.file_name.set('Файл не выбран')
                    self.browse_button.configure(state='normal')
                    self.status.set('BIN не подходит: '+text)
                    messagebox.showerror('BIN не подходит', text, parent=self.root)
                elif kind == 'progress':
                    self.status.set(text)
                    self.progress['value'] = percent
                else:
                    self.busy = False
                    self.refresh_button.configure(state='normal')
                    self.browse_button.configure(state='normal')
                    self.combo.configure(state='readonly')
                    # Require a fresh selection before another write, especially after errors.
                    self.combo.set('')
                    if kind == 'error':
                        self.status.set('Установка не подтверждена. '+text)
                        messagebox.showerror('Не удалось завершить установку', text, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('Дождитесь завершения', 'Сейчас идёт обновление или проверка. Не отключайте очки.', parent=self.root)
        else:
            self.root.destroy()


def main():
    if any(flag in sys.argv for flag in ('--self-test', '--check-device', '--verify-device')):
        # Diagnostics never issue firmware-writing commands.
        mode = next(flag for flag in ('--verify-device', '--check-device', '--self-test') if flag in sys.argv)
        dest = Path(sys.argv[sys.argv.index(mode)+1])
        chosen_path = Path(sys.argv[sys.argv.index('--bin')+1]) if '--bin' in sys.argv else None
        reports = {}
        selected_firmware = backend.identify_package(chosen_path) if chosen_path else None
        candidates = ((selected_firmware.key, selected_firmware),) if chosen_path else backend.FIRMWARES.items()
        for key, firmware in candidates:
            data, expected = backend.load_package(chosen_path, firmware=firmware)
            reports[key] = {'bytes': len(data), 'verified_files': len(expected)}
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        root.update_idletasks()
        found = backend.devices()
        count = len(found)
        root.destroy()
        device_identity = None
        if mode in ('--check-device', '--verify-device'):
            if count != 1:
                raise RuntimeError('Read-only check requires exactly one device')
            with backend.SelectedProbe(found[0]) as probe:
                if selected_firmware is None:
                    selected_firmware = (backend.FIRMWARES.get(
                        sys.argv[sys.argv.index('--firmware')+1], backend.STANDARD
                    ) if '--firmware' in sys.argv else backend.STANDARD)
                device_identity = backend.identity(probe, selected_firmware)
            if mode == '--verify-device':
                _, expected = backend.load_package(chosen_path, firmware=selected_firmware)
                backend.verify_selected(found[0], expected, lambda *_: None,
                                        firmware=selected_firmware)
        dest.write_text(json.dumps({'ok': True, 'firmwares': reports,
                                    'hid_devices': count, 'tk_loaded': True, 'firmware_writes': 0,
                                    'device_identity': device_identity}), encoding='utf-8')
        return
    if sys.platform == 'win32':
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\XREALRussianUpdater')
        if not mutex:
            raise OSError('Cannot create application lock')
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(None, 'Приложение уже запущено.', 'XREAL', 0)
            return
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
