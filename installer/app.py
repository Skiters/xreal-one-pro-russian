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
        root.title('XREAL One Pro · Русское меню · v0.1.1')
        root.geometry('720x510')
        root.minsize(640, 490)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style()
        style.theme_use('vista' if 'vista' in style.theme_names() else 'clam')
        style.configure('TLabel', font=('Segoe UI', 11))
        style.configure('TButton', font=('Segoe UI', 11), padding=8)
        frame = ttk.Frame(root, padding=28)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Русское меню для XREAL One Pro', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Подключите очки по USB → выберите устройство → установите прошивку.',
                  wraplength=650).pack(anchor='w', pady=(10, 18))
        ttk.Label(frame, text='1. Выберите очки').pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(8, 16))
        self.combo = ttk.Combobox(row, state='readonly', font=('Segoe UI', 11))
        self.combo.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.combo.bind('<<ComboboxSelected>>', self.selected)
        self.refresh_button = ttk.Button(row, text='Обновить список', command=self.refresh)
        self.refresh_button.pack(side='right')
        ttk.Label(frame, text='Русский заменит польский язык. Английский сохранится.\n'
                  'Прошивка встроена в приложение. Интернет для установки не нужен.',
                  wraplength=640).pack(anchor='w')
        ttk.Label(frame, text='Экспериментальная сборка для Pilot 1.9.1.20260914175625.\n'
                  'Закройте вкладку обновления XREAL и не отключайте кабель во время записи.',
                  foreground='#6b4c16', wraplength=640).pack(anchor='w', pady=(10, 14))
        self.install_button = ttk.Button(frame, text='2. Установить русскую прошивку', command=self.start, state='disabled')
        self.install_button.pack(anchor='w')
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill='x', pady=(18, 10))
        self.status = tk.StringVar(value='Поиск подключённых очков…')
        ttk.Label(frame, textvariable=self.status, wraplength=640).pack(anchor='w')
        root.after(100, self.refresh)
        root.after(100, self.poll)

    def selected(self, _event=None):
        self.install_button.configure(state='normal' if self.combo.current() >= 0 and not self.busy else 'disabled')

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
            self.status.set('Выберите очки в списке.' if self.found else
                            'Очки не найдены. Подключите кабель с передачей данных и нажмите «Обновить список».')
        except Exception as e:
            self.status.set('Не удалось получить список USB-устройств: '+str(e))

    def start(self):
        index = self.combo.current()
        if self.busy or index < 0:
            return
        selected = self.found[index].copy()
        self.busy = True
        for widget in [self.install_button, self.refresh_button, self.combo]:
            widget.configure(state='disabled')
        self.progress['value'] = 0
        self.status.set('Подготовка…')
        threading.Thread(target=self.worker, args=(selected,), daemon=False).start()

    def worker(self, selected):
        # Hold the machine awake during transmission and read-back, on this same thread.
        try:
            if sys.platform == 'win32':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            backend.update(selected, lambda text, percent: self.events.put(('progress', text, percent)))
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
    if '--self-test' in sys.argv or '--check-device' in sys.argv:
        # Packaged-runtime smoke test, never opens or writes a HID device.
        mode = '--check-device' if '--check-device' in sys.argv else '--self-test'
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
        if mode == '--check-device':
            if count != 1:
                raise RuntimeError('Read-only check requires exactly one device')
            with backend.SelectedProbe(found[0]) as probe:
                device_identity = backend.identity(probe)
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
