import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from app import find_codex
from dashboard_core import DEFAULT_CONFIG, merge_config
from tools.build_release import FILES
from tools.check_release import check


class ReleaseTests(unittest.TestCase):
    def test_public_files_and_sample_settings_are_complete(self):
        check()
        config=merge_config(json.loads((ROOT / "settings.example.json").read_text()))
        self.assertEqual(config,DEFAULT_CONFIG)

    def test_release_does_not_include_local_files(self):
        self.assertNotIn("settings.json",FILES)
        self.assertFalse(any(p.endswith(".lnk") or p.startswith(("vendor/","runtime/",".venv/")) for p in FILES))

    def test_npm_cmd_launcher_is_started_through_node(self):
        package=pathlib.Path("C:/npm/node_modules/@openai/codex/bin/codex.js")
        def which(name):
            return {"codex":"C:/npm/codex.cmd","node":"C:/node/node.exe"}.get(name)
        with patch("app.shutil.which",side_effect=which), patch("app.pathlib.Path.is_file",return_value=True):
            self.assertEqual(find_codex(),["C:/node/node.exe",str(package)])

    def test_native_codex_executable_is_used_directly(self):
        with patch("app.shutil.which",return_value="C:/apps/codex.exe"):
            self.assertEqual(find_codex(),"C:/apps/codex.exe")


if __name__=="__main__":
    unittest.main()
