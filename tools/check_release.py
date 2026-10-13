"""Check the exact files intended for GitHub and source downloads."""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from dashboard_core import merge_config
from tools.build_release import FILES


def check(root=ROOT):
    for name in FILES:
        path = root / name
        if not path.is_file():
            raise AssertionError("Missing release file: " + name)
        if path.suffix.lower() in (".py", ".md", ".json", ".ps1", ".cmd", ".yml"):
            body = path.read_text(encoding="utf-8-sig")
            if re.search(r"[A-Za-z]:[/\\]Users[/\\][^\s/'\"<>]+",body,re.I):
                raise AssertionError("Personal Windows path in " + name)
            if re.search(r"\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}",body):
                raise AssertionError("Possible credential in " + name)
    example = merge_config(json.loads((root / "settings.example.json").read_text()))
    if example["rotation"] != 0 or not example["remaining"]:
        raise AssertionError("Public defaults must be upright and show quota remaining")
    readme = (root / "README.md").read_text(encoding="utf-8")
    if "docs/display-example.png" not in readme:
        raise AssertionError("README is missing its display example")
    if any(name.startswith(("vendor/","runtime/",".venv/",".codex/")) or name.endswith(".lnk") or name=="settings.json" for name in FILES):
        raise AssertionError("Private/generated file in release list")
    print("Release files verified: sample settings and display image; no local state, shortcuts, binaries or detected credentials.")


if __name__ == "__main__":
    check()
