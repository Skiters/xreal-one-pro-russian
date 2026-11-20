"""Portable read-only collector; no console, no auto-upload, no firmware writes."""
from datetime import datetime
from pathlib import Path
import queue
import threading
import sys
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import dump_core


def main():
    root = tk.Tk(); root.title('XREAL · Сбор дампа 0.1')
    root.geometry(f'{min(780,root.winfo_screenwidth()-60)}x{min(580,root.winfo_screenheight()-100)}')
    shell = ttk.Frame(root, padding=12); shell.pack(fill='both',expand=True)
    canvas=tk.Canvas(shell,highlightthickness=0)
    scroll=ttk.Scrollbar(shell,orient='vertical',command=canvas.yview)
    scroll.pack(side='right',fill='y');canvas.pack(fill='both',expand=True)
    canvas.configure(yscrollcommand=scroll.set)
    frame=ttk.Frame(canvas,padding=8);canvas.create_window(0,0,window=frame,anchor='nw')
    frame.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
    ttk.Label(frame,text='Скопировать доступные файлы с очков',font=('Segoe UI',16,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Программа только читает данные. Полнота зависит от модели и прошивки.\n'
              'Это не образ всей памяти и не гарантия возможности восстановления.\n'
              'Содержимое сохраняется локально; перед отправкой проверьте файлы:\n'
              'дамп может содержать настройки и идентификаторы.',wraplength=620).pack(anchor='w',pady=12)
    found=[]; events=queue.Queue(); stop=threading.Event(); busy=[False]
    combo=ttk.Combobox(frame,state='readonly',width=65);combo.pack(fill='x')
    status=tk.StringVar(value='Подключите очки кабелем с передачей данных.')
    def refresh():
        try:
            found[:]=dump_core.devices()
            combo['values']=[f'{d.get("product_string") or "XREAL"} · PID {d["product_id"]:04x} · устройство {i+1}' for i,d in enumerate(found)]
            if found:combo.current(0)
            else:combo.set('');status.set('Совместимый HID-интерфейс не найден. Эта модель может не поддерживаться.')
        except Exception as e:status.set(str(e))
    refresh_button=ttk.Button(frame,text='Обновить список',command=refresh);refresh_button.pack(anchor='w',pady=8)
    def collect():
        if combo.current()<0:return
        folder=filedialog.askdirectory(title='Куда сохранить дамп?')
        if not folder:return
        dest=Path(folder)/datetime.now().strftime('XREAL-dump-%Y%m%d-%H%M%S-%f')
        selected=found[combo.current()].copy();stop.clear();busy[0]=True
        start.config(state='disabled');refresh_button.config(state='disabled');combo.config(state='disabled');cancel.config(state='normal')
        def worker():
            try:
                report=dump_core.capture(selected,dest,lambda m:events.put(('status',m)),stop)
                events.put(('done',f'Сбор: {report["state"]}. Полный дамп не подтверждён.\nПапка: {dest}\nОтчёт: report.json'))
            except Exception as e:events.put(('done',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    start=ttk.Button(frame,text='Собрать дамп…',command=collect);start.pack(anchor='w',pady=8)
    cancel=ttk.Button(frame,text='Остановить после текущего чтения',state='disabled',command=stop.set);cancel.pack(anchor='w')
    ttk.Label(frame,textvariable=status,wraplength=620).pack(anchor='w',pady=16)
    ttk.Label(frame,text='Поддерживается протокол экспорта XREAL VID 3318.\n'
              'Новые PID обнаруживаются автоматически, но это не подтверждает совместимость.\n'
              'Другие бренды и неизвестные протоколы не обслуживаются.\n'
              'При остановке текущий запрос может завершаться до нескольких минут.',wraplength=620).pack(anchor='w')
    def poll():
        while not events.empty():
            kind,text=events.get();status.set(text)
            if kind=='done':
                busy[0]=False;start.config(state='normal');refresh_button.config(state='normal');combo.config(state='readonly');cancel.config(state='disabled')
        root.after(150,poll)
    def close():
        if busy[0]:stop.set();status.set('Останавливаем сбор. Дождитесь завершения текущего чтения.')
        else:root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    refresh();poll();root.mainloop()


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--self-test':
        root=tk.Tk();root.withdraw();root.update();root.destroy()
        Path(sys.argv[2]).write_text(json.dumps({'tk':True,'hid_enumeration':len(dump_core.devices()),'device_commands_sent':0}),encoding='utf-8')
    else:main()
