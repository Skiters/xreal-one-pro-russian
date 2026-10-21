"""Standalone Windows GUI. Merely opening this app never starts a write."""
import ctypes
from datetime import datetime, timezone
import hashlib
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
import diagnostics
from version import VERSION


class App:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.events = queue.Queue()
        self.found = []
        self.build = None
        self.last_support = None
        root.title(f'XREAL One / One Pro / 1S · Русское меню · v{VERSION}')
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
        ttk.Label(frame, text='Русское меню для XREAL One / One Pro / 1S', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
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
        self.diagnostic_button = ttk.Button(frame, text='Диагностика / отчёт об ошибке…', command=self.show_diagnostics)
        self.diagnostic_button.pack(anchor='w', pady=(6, 0))
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

    def show_diagnostics(self):
        if self.busy:
            return
        window = tk.Toplevel(self.root)
        window.title('Диагностика XREAL — отчёт для поддержки')
        window.geometry('700x540')
        window.minsize(320, 240)
        ttk.Label(window, text='Отчёт открыт для просмотра. Копирование и сохранение — по вашей кнопке. '
                  'Серийный номер, имя компьютера и пути к вашим файлам не включаются. '
                  'Проверка ничего не прошивает и ничего не отправляет в интернет.', wraplength=650).pack(fill='x', padx=12, pady=8)
        area = ttk.Frame(window)
        area.pack(fill='both', expand=True, padx=12)
        text = tk.Text(area, wrap='word', font=('Consolas', 10), width=30, height=10)
        scroll = ttk.Scrollbar(area, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        def display(value):
            text.configure(state='normal')
            text.delete('1.0', 'end')
            text.insert('1.0', value)
            text.configure(state='disabled')
        display(diagnostics.render(self.last_support) if self.last_support else
                'Нет отчёта за этот запуск. Нажмите «Проверить сейчас». BIN выбирать необязательно.')
        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(text.get('1.0', 'end-1c'))
        def save():
            path = filedialog.asksaveasfilename(parent=window, defaultextension='.txt',
                initialfile='XREAL-diagnostic.txt', filetypes=[('Текстовый отчёт', '*.txt')])
            if path:
                try:
                    Path(path).write_text(text.get('1.0', 'end-1c'), encoding='utf-8')
                except OSError as error:
                    messagebox.showerror('Не удалось сохранить', str(error), parent=window)
        def run():
            if self.busy:
                return
            self.busy = True
            self.selected()
            for control in (self.refresh_button, self.browse_button, self.combo):
                control.configure(state='disabled')
            run_button.configure(state='disabled')
            previous = self.last_support
            index = self.combo.current()
            selected = self.found[index].copy() if index >= 0 else None
            path = self.firmware_path
            display('Проверка USB, версий и совместимости…')
            results = queue.Queue()
            def work():
                results.put(diagnostics.collect(selected, path))
            def finish():
                try:
                    value = results.get_nowait()
                except queue.Empty:
                    self.root.after(100, finish)
                    return
                self.busy = False
                self.last_support = {'last_operation': previous.get('last_operation', previous), 'diagnostic': value} if previous else value
                self.refresh_button.configure(state='normal')
                self.browse_button.configure(state='normal')
                self.combo.configure(state='readonly')
                self.selected()
                if window.winfo_exists():
                    display(diagnostics.render(self.last_support))
                    run_button.configure(state='normal')
            threading.Thread(target=work, daemon=True).start()
            self.root.after(100, finish)
        buttons = ttk.Frame(window)
        buttons.pack(fill='x', padx=12, pady=8)
        run_button = ttk.Button(buttons, text='Проверить сейчас', command=run)
        run_button.pack(anchor='w')
        ttk.Button(buttons, text='Скопировать отчёт', command=copy).pack(anchor='w')
        ttk.Button(buttons, text='Сохранить TXT…', command=save).pack(anchor='w')

    def layout(self, _event=None):
        available = max(1, self.canvas.winfo_width())
        # On unusually narrow/high-DPI displays keep every control reachable
        # through horizontal scrolling instead of clipping a button.
        minimum = max(button.winfo_reqwidth() for button in
                      (self.install_button, self.verify_button, self.refresh_button, self.browse_button, self.diagnostic_button)) + 40
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
        if event.widget.winfo_toplevel() != self.root:
            return
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
        elif self.firmware is backend.ONE_S_STANDARD:
            self.description.configure(text='XREAL 1S: русификация собственной штатной Pilot 1.9.1. '
                'Без переноса функций Pro+, без изменений драйверов, разрешения и яркости. '
                'На физической 1S пока не проверена.')
        elif self.firmware is backend.ONE_EXPERIMENTAL:
            self.description.configure(text='Тестовая Pilot 2.0 только для обычных XREAL One. '
                                       'Русское меню, AI HDR и Real3D 2D→3D с CPU-обработкой. '
                                       'Настройки панели One, без профиля яркости Hebe и разгона. '
                                       'На обычном One работа этих функций ещё не проверена; '
                                       'плавность и глубина требуют проверки владельцем.')
        elif self.firmware is backend.EXPERIMENTAL:
            self.description.configure(text='Для One Pro X1112010 / X1112011. На X1112011 пока не проверено. Русское меню, AI HDR, '
                                       'режимы «Чтение» и «Кино», профиль Hebe с luminance до 500 '
                                       'и экспериментальный Real3D с CPU-обработкой. '
                                       'Плавность зависит от сборки; стабильные 60 кадров/с не подтверждены.')
        else:
            self.description.configure(text='Выберите BIN-файл подходящей версии Pilot.')
        if self.build:
            self.description.configure(text=self.description.cget('text') +
                f"\nСборка {self.build.get('release', '?')} · {self.build['build_id'][:12]}. "
                'Полный 3D LUT, согласованное смещение Y/U/V, постоянный пул потоков. '
                'Перед записью проверяются системные библиотеки и карта видеопамяти. '
                'Результаты проверки опубликованных BIN смотрите в описании выпуска.')
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
        self.build = None
        self.selected()
        self.busy = True
        self.browse_button.configure(state='disabled')
        self.file_name.set(Path(path).name)
        self.status.set('Проверка BIN и контрольных сумм…')
        threading.Thread(target=self.check_firmware, args=(path,), daemon=True).start()

    def check_firmware(self, path):
        try:
            firmware, build = backend.identify_package(path, return_manifest=True)
            self.events.put(('file_ok', path, (firmware, build)))
        except Exception as error:
            self.events.put(('support', diagnostics.report('bin', 'validation', error=error), 0))
            self.events.put(('file_error', str(error), 0))

    def refresh(self):
        if self.busy:
            return
        try:
            self.found = backend.devices()
            self.combo['values'] = [f"XREAL {({0x0438: 'One', 0x0436: 'One Pro', 0x043e: '1S'}).get(d.get('product_id'), 'Unknown')} — устройство {i+1}" +
                                    (f" · {d['serial_number'][-6:]}" if d.get('serial_number') else '')
                                    for i, d in enumerate(self.found)]
            self.combo.set('')
            self.install_button.configure(state='disabled')
            self.verify_button.configure(state='disabled')
            self.status.set('Выберите очки в списке.' if self.found else
                            'Очки не найдены. Подключите кабель с передачей данных и нажмите «Обновить список».')
        except Exception as e:
            self.last_support = diagnostics.report('diagnostic', 'usb', error=e)
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
        report = {'version': VERSION, 'started_utc': datetime.now(timezone.utc).isoformat(),
                  'firmware': firmware.key, 'filename': Path(path).name if path else firmware.filename,
                  'usb_pid': selected.get('product_id'), 'operation': 'verify' if verify_only else 'update',
                  'selected_build': self.build, 'ok': False}
        def progress(text, percent):
            report['last_stage'] = text
            report['progress'] = percent
            self.events.put(('progress', text, percent))
        try:
            if path:
                with Path(path).open('rb') as source:
                    report['package_sha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
            if sys.platform == 'win32':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            action = backend.verify_only if verify_only else backend.update
            result = action(selected, progress, firmware=firmware, path=path)
            report.update(result or {})
            report['ok'] = True
            self.events.put(('done', '', 100))
        except Exception as e:
            report['error'] = str(e)
            report['exception'] = type(e).__name__
            log = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'XREAL-Russian-Updater' / 'last-error.txt'
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(traceback.format_exc(), encoding='utf-8')
            except OSError:
                pass
        finally:
            progress_value = report.get('progress', 0)
            stage = 'readback' if progress_value >= 90 else 'restart' if progress_value >= 89 else 'apply' if progress_value >= 86 else 'transfer' if progress_value >= 1 else 'preflight'
            support = diagnostics.report(report['operation'], stage, ok=report['ok'],
                error=report.get('error'), exception=report.get('exception'), usb_pid=f"{selected.get('product_id', 0):04x}",
                firmware=firmware.key, package_sha256=report.get('package_sha256'),
                progress=progress_value, build_id=(report.get('selected_build') or {}).get('build_id'))
            support['last_stage'] = diagnostics.clean(report.get('last_stage', ''))
            self.events.put(('support', support, 0))
            if not report['ok']:
                self.events.put(('error', support['code'] + ': ' + report.get('error', ''), 0))
            report['finished_utc'] = datetime.now(timezone.utc).isoformat()
            try:
                folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'XREAL-Russian-Updater'
                folder.mkdir(parents=True, exist_ok=True)
                name = datetime.now(timezone.utc).strftime('report-%Y%m%d-%H%M%S-%f.json')
                (folder/name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                (folder/'support-report.txt').write_text(diagnostics.render(support), encoding='utf-8')
            except OSError:
                pass
            if sys.platform == 'win32':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

    def poll(self):
        try:
            while True:
                kind, text, percent = self.events.get_nowait()
                if kind == 'support':
                    self.last_support = text
                elif kind == 'file_ok':
                    self.busy = False
                    self.firmware, self.build = percent
                    self.firmware_path = Path(text)
                    self.browse_button.configure(state='normal')
                    self.status.set('BIN проверен. Выберите очки и запустите установку.')
                    self.firmware_changed()
                elif kind == 'file_error':
                    self.busy = False
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
                        messagebox.showerror('Не удалось завершить установку', text + '\nОткройте «Диагностика / отчёт об ошибке» и скопируйте отчёт.', parent=self.root)
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
            reports[key] = {'bytes': len(data), 'verified_files': len(expected),
                            'package_sha256': expected.package_sha256, 'build': expected.build}
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        root.update_idletasks()
        found = backend.devices()
        count = len(found)
        root.destroy()
        device_identity = None
        abi_preflight = None
        if mode in ('--check-device', '--verify-device'):
            if count != 1:
                raise RuntimeError('Read-only check requires exactly one device')
            with backend.SelectedProbe(found[0]) as probe:
                if selected_firmware is None:
                    selected_firmware = (backend.FIRMWARES.get(
                        sys.argv[sys.argv.index('--firmware')+1], backend.STANDARD
                    ) if '--firmware' in sys.argv else backend.STANDARD)
                device_identity = backend.identity(probe, selected_firmware)
                _, expected = backend.load_package(chosen_path, firmware=selected_firmware)
                if expected.build:
                    abi_preflight = backend.check_abi(probe, expected.build)
            if mode == '--verify-device':
                _, expected = backend.load_package(chosen_path, firmware=selected_firmware)
                backend.verify_selected(found[0], expected, lambda *_: None,
                                        firmware=selected_firmware)
        dest.write_text(json.dumps({'ok': True, 'firmwares': reports,
                                    'hid_devices': count, 'tk_loaded': True, 'firmware_writes': 0,
                                    'version': VERSION, 'abi_preflight': abi_preflight,
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
    try:
        main()
    except Exception as error:
        mode = next((flag for flag in ('--self-test','--check-device','--verify-device')
                     if flag in sys.argv), None)
        if not mode:
            raise
        destination = Path(sys.argv[sys.argv.index(mode)+1])
        destination.write_text(json.dumps({'ok': False, 'version': VERSION,
                                           'error': str(error), 'firmware_writes': 0},
                                          ensure_ascii=False, indent=2), encoding='utf-8')
        raise SystemExit(1)
