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
from tkinter import ttk, messagebox

import backend


class App:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.events = queue.Queue()
        self.found = []
        root.title('XREAL One Pro · Русское меню · v0.1.3')
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
        ttk.Label(frame, text='Русское меню для XREAL One Pro', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Подключите очки по USB → выберите устройство → установите прошивку.',
                  wraplength=650).pack(anchor='w', pady=(10, 18))
        ttk.Label(frame, text='1. Выберите очки').pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(8, 16))
        self.combo = ttk.Combobox(row, state='readonly', font=('Segoe UI', 11))
        self.combo.pack(fill='x')
        self.combo.bind('<<ComboboxSelected>>', self.selected)
        self.refresh_button = ttk.Button(row, text='Обновить список', command=self.refresh)
        self.refresh_button.pack(anchor='w', pady=(6, 0))
        ttk.Label(frame, text='Русский заменит польский язык. Английский сохранится.\n'
                  'Прошивка встроена в приложение. Интернет для установки не нужен.',
                  wraplength=640).pack(anchor='w')
        ttk.Label(frame, text='Экспериментальная сборка для Pilot 1.9.1.20260914175625.\n'
                  'Закройте вкладку обновления XREAL и не отключайте кабель во время записи.',
                  foreground='#6b4c16', wraplength=640).pack(anchor='w', pady=(10, 14))
        self.install_button = ttk.Button(frame, text='2. Установить прошивку', command=self.start, state='disabled')
        self.install_button.pack(anchor='w')
        self.verify_button = ttk.Button(frame, text='Проверить без записи', command=lambda: self.start(verify_only=True), state='disabled')
        self.verify_button.pack(anchor='w', pady=(6, 0))
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill='x', pady=(18, 10))
        self.status = tk.StringVar(value='Поиск подключённых очков…')
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=640)
        self.status_label.pack(anchor='w')
        self.labels = [child for child in frame.winfo_children() if isinstance(child, ttk.Label)]
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
                      (self.install_button, self.verify_button, self.refresh_button)) + 40
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

    def selected(self, _event=None):
        self.install_button.configure(state='normal' if self.combo.current() >= 0 and not self.busy else 'disabled')
        self.verify_button.configure(state='normal' if self.combo.current() >= 0 and not self.busy else 'disabled')

    def refresh(self):
        if self.busy:
            return
        try:
            self.found = backend.devices()
            self.combo['values'] = [f"XREAL One Pro — устройство {i+1}" +
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
        for widget in [self.install_button, self.verify_button, self.refresh_button, self.combo]:
            widget.configure(state='disabled')
        self.progress['value'] = 0
        self.status.set('Подготовка…')
        threading.Thread(target=self.worker, args=(selected, verify_only), daemon=False).start()

    def worker(self, selected, verify_only=False):
        # Hold the machine awake during transmission and read-back, on this same thread.
        try:
            if sys.platform == 'win32':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            action = backend.verify_only if verify_only else backend.update
            action(selected, lambda text, percent: self.events.put(('progress', text, percent)))
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
                if kind == 'progress':
                    self.status.set(text)
                    self.progress['value'] = percent
                else:
                    self.busy = False
                    self.refresh_button.configure(state='normal')
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
        data, expected = backend.load_package()
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
                device_identity = backend.identity(probe)
            if mode == '--verify-device':
                backend.verify_selected(found[0], expected, lambda *_: None)
        dest.write_text(json.dumps({'ok': True, 'bytes': len(data), 'verified_files': len(expected),
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
