"""Exercise real Tk geometry with small viewports and Windows DPI scales."""
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import App


class LayoutTests(unittest.TestCase):
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
                        app.mousewheel(SimpleNamespace(delta=-120))
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
