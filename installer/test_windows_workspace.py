"""Exercise the preview with simulated devices and process-local Tk scaling."""

import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if os.name == "nt":
    import windows_workspace as ui


@unittest.skipUnless(os.name == "nt", "Windows workspace")
class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0)
        self.scan = self.enterContext(
            patch.object(ui.dump_core, "devices", return_value=[])
        )
        self.operation = Mock()
        self.app = ui.Workspace(self.root, self.operation)
        self.root.update()

    def tearDown(self):
        self.root.after_cancel(self.app.timer)
        self.root.destroy()

    def finish(self):
        end = time.monotonic() + 5
        while self.app.activity and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app.activity)

    def test_compact_footer_at_four_dpi_settings(self):
        for dpi in (96, 120, 144, 192):
            self.root.after_cancel(self.app.timer)
            self.root.destroy()
            self.root = tk.Tk()
            self.root.attributes("-alpha", 0)
            self.root.tk.call("tk", "scaling", dpi / 72)
            self.app = ui.Workspace(self.root, self.operation)
            self.root.geometry(f"{int(395 * dpi / 96)}x{int(345 * dpi / 96)}")
            self.root.update()
            for theme in ("light", "dark"):
                self.app.theme.set(theme)
                self.app.colors()
                self.root.update()
                self.assertTrue(self.app.selector.winfo_ismapped())
                self.assertFalse(self.app.nav.winfo_ismapped())
                self.assertIn("подключите очки", self.app.notice.get())
                for w in (self.app.status_label, self.app.install):
                    self.assertLessEqual(
                        w.winfo_rooty() + w.winfo_height(),
                        self.root.winfo_rooty() + self.root.winfo_height(),
                    )
                self.assertTrue(self.app.install.instate(["disabled"]))
                self.app.selector.focus_force()
                self.root.update()
                self.app.selector.event_generate("<Tab>")
                self.root.update()
                self.assertIsNotNone(self.root.focus_get())
                self.assertIsNot(self.root.focus_get(), self.app.selector)

    def test_preflight_and_confirmation(self):
        self.scan.return_value = [
            {"path": b"a", "product_id": 0x436},
            {"path": b"b", "product_id": 0x436},
        ]
        self.app.refresh()
        self.assertIsNone(self.app.device)
        self.app.file = Path("C:/") / ("long-path-" * 15) / ("One-Pro-" * 17 + ".bin")
        self.app.firmware = Mock()
        self.app.combo.current(1)
        with patch.object(
            self.app, "collect", return_value={"ok": False, "sku": "X1112099"}
        ):
            self.app.select(None)
            self.finish()
        self.assertFalse(self.app.ready)
        self.assertIn("Дамп", self.app.notice.get())
        with patch.object(self.app, "collect", return_value={"ok": True}):
            self.app.preflight()
            self.finish()
        self.assertTrue(self.app.ready)
        with patch.object(ui.messagebox, "askokcancel", return_value=False) as confirm:
            self.app.operate(False)
            self.assertIn(self.app.file.name, confirm.call_args.args[1])
            self.assertIn("One Pro", confirm.call_args.args[1])
        self.operation.assert_not_called()

    def test_unknown_device_can_cancel_and_open_partial_dump(self):
        self.scan.return_value = [{"product_id": 0x9999, "path": b"a"}]
        self.app.refresh()
        self.assertFalse(self.app.ready)
        self.app.page.set(ui.PAGES[2])
        self.app.render()

        def capture(device, folder, emit, stop):
            folder.mkdir()
            emit("Чтение")
            self.assertTrue(stop.wait(3))
            (folder / "report.json").write_text("{}")
            return {"state": "cancelled"}

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(ui.dump_core, "capture", side_effect=capture),
            patch.object(ui.filedialog, "askdirectory", return_value=tmp),
        ):
            self.app.dump()
            self.app.cancel()
            self.finish()
            self.assertIn("Частичный результат", self.app.notice.get())
            self.assertTrue((self.app.folder / "report.json").exists())
            with patch.object(os, "startfile") as explorer:
                self.app.open_folder()
                explorer.assert_called_once_with(self.app.folder)

    def test_diagnostic_report_is_viewable_copyable_and_saved(self):
        self.app.page.set(ui.PAGES[1])
        self.app.render()
        self.app.diagnose()
        self.finish()
        text = self.app.report_box.get("1.0", "end-1c")
        self.assertIn("XREAL-DIAG-1", text)
        self.assertIn("no-device", text)
        self.app.copy()
        self.assertEqual(self.root.clipboard_get(), text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.txt"
            with patch.object(
                ui.filedialog, "asksaveasfilename", return_value=str(path)
            ):
                self.app.save()
            self.assertEqual(path.read_text("utf8"), text)

    def test_readback_has_distinct_success_message(self):
        self.scan.return_value = [{"product_id": 0x436, "path": b"a"}]
        self.app.refresh()
        self.app.file = Path("test.bin")
        self.app.firmware = ui.backend.EXPERIMENTAL
        with patch.object(self.app, "collect", return_value={"ok": True}):
            self.app.preflight()
            self.finish()

        def readback(device, firmware, path, verify, build, events):
            self.assertTrue(verify)
            events.put(("progress", "Обратное чтение", 94))
            events.put(("support", {"ok": True}, 0))

        self.operation.side_effect = readback
        self.app.operate(True)
        self.finish()
        self.assertEqual(self.app.notice.get(), "Установленные файлы сверены.")
