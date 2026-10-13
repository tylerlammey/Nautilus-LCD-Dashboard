import copy
import json
import pathlib
import sys
import threading
import unittest
import uuid

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app import ControlServer, Dashboard
from http.server import BaseHTTPRequestHandler
from dashboard_core import DEFAULT_CONFIG


class SettingsTests(unittest.TestCase):
    def test_control_port_cannot_have_two_dashboard_listeners(self):
        first=ControlServer(("127.0.0.1",0),BaseHTTPRequestHandler)
        try:
            with self.assertRaises(OSError):
                ControlServer(("127.0.0.1",first.server_port),BaseHTTPRequestHandler)
        finally:
            first.server_close()

    def test_hot_reload_keeps_last_valid_settings(self):
        directory=ROOT / "runtime"
        directory.mkdir(exist_ok=True)
        folder=directory / ("settings-test-" + uuid.uuid4().hex)
        folder.mkdir()
        self.assertTrue(folder.resolve().is_relative_to(ROOT))
        try:
            dashboard=Dashboard.__new__(Dashboard)
            dashboard.config=copy.deepcopy(DEFAULT_CONFIG)
            dashboard.config_path=pathlib.Path(folder) / "settings.json"
            dashboard.lock=threading.RLock()
            dashboard._config_signature=None
            dashboard.config_path.write_text(json.dumps({"theme":{"cpu":"#abcdef"},"layout":{"show_reset_times":True}}))
            dashboard.reload_config()
            self.assertEqual(dashboard.config["theme"]["cpu"],"#abcdef")
            self.assertTrue(dashboard.config["layout"]["show_reset_times"])
            dashboard.config_path.write_text('{"theme":')
            with self.assertLogs(level="WARNING"):
                dashboard.reload_config()
            self.assertEqual(dashboard.config["theme"]["cpu"],"#abcdef")
            dashboard.config_path.write_text(json.dumps({"theme":{"cpu":"#112233"},"rotation":180}))
            dashboard.reload_config()
            self.assertEqual(dashboard.config["theme"]["cpu"],"#112233")
            self.assertEqual(dashboard.config["rotation"],180)
        finally:
            (folder / "settings.json").unlink(missing_ok=True)
            folder.rmdir()


if __name__=="__main__":
    unittest.main()
