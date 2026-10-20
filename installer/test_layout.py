"""Exercise real Tk geometry with small viewports and Windows DPI scales."""
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import App


class LayoutTests(unittest.TestCase):
    def test_diagnostic_dialog_displays_and_copies_visible_report(self):
        from tkinter import ttk
        import diagnostics
        root = tk.Tk()
        root.withdraw()
        try:
            with patch('backend.devices', return_value=[]), patch('backend.update') as update:
                app = App(root)
                app.last_support = diagnostics.report('update', 'transfer', error=OSError('USB disconnected'))
                app.show_diagnostics()
                root.update_idletasks()
                dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
                def children(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from children(child)
                widgets = list(children(dialog))
                text = next(w for w in widgets if isinstance(w, tk.Text))
                self.assertIn('XR-UPDATE-TRANSFER', text.get('1.0', 'end'))
                button = next(w for w in widgets if isinstance(w, ttk.Button) and
                              w.cget('text') == 'Скопировать отчёт')
                button.invoke()
                self.assertEqual(root.clipboard_get(), text.get('1.0', 'end-1c'))
                update.assert_not_called()
        finally:
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
            root.destroy()

    def test_small_windows_at_three_dpi_scales(self):
        for dpi in (96, 144, 192):
            with self.subTest(dpi=dpi):
                root = tk.Tk()
                root.attributes('-alpha', 0)
                root.tk.call('tk', 'scaling', dpi / 72)
                try:
                    with patch('backend.devices', return_value=[]):
                        app = App(root)
                        root.geometry('420x280')
                        root.update()
                        self.assertGreater(app.content.winfo_height(), app.canvas.winfo_height())
                        app.canvas.yview_moveto(0)
                        app.mousewheel(SimpleNamespace(delta=-120, widget=app.canvas))
                        self.assertGreater(app.canvas.yview()[0], 0)
                        app.canvas.yview_moveto(1)
                        root.update_idletasks()
                        bottom = app.status_label.winfo_rooty()+app.status_label.winfo_height()
                        viewport_bottom = app.canvas.winfo_rooty()+app.canvas.winfo_height()
                        self.assertLessEqual(bottom, viewport_bottom+2)
                        app.canvas.yview_moveto(0)
                        app.reveal_focus(SimpleNamespace(widget=app.verify_button))
                        root.update_idletasks()
                        self.assertGreaterEqual(app.verify_button.winfo_rooty(), app.canvas.winfo_rooty())
                        self.assertLessEqual(app.verify_button.winfo_rooty()+app.verify_button.winfo_height(),
                                             app.canvas.winfo_rooty()+app.canvas.winfo_height())
                        for label in app.labels:
                            self.assertLessEqual(label.winfo_width(), app.content.winfo_width())
                finally:
                    for timer in root.tk.call('after', 'info'):
                        root.after_cancel(timer)
                    root.destroy()


if __name__ == '__main__':
    unittest.main()
