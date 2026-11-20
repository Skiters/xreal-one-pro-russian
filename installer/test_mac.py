import unittest
from unittest.mock import patch

import backend


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.selected = dict(product_id=0x436, serial_number='A', path=b'old')
        self.returned = dict(self.selected, path=b'new')

    def test_new_path_requires_unique_model_and_serial(self):
        other = dict(self.returned, serial_number='B')
        with patch('sys.platform', 'darwin'), patch('backend.devices', return_value=[other, self.returned]):
            self.assertEqual(backend.readback_device(self.selected), self.returned)
        for ambiguous in ([other], [self.returned, self.returned]):
            with patch('sys.platform', 'darwin'), patch('backend.devices', return_value=ambiguous):
                with self.assertRaises(OSError):
                    backend.readback_device(self.selected)

    def test_no_serial_keeps_original_path(self):
        device = dict(self.selected, serial_number='')
        with patch('sys.platform', 'darwin'):
            self.assertEqual(backend.readback_device(device), device)
