"""Build a source ZIP from an explicit list; never copy local state or binaries."""
import argparse
import hashlib
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = (
    ".gitignore", ".gitattributes", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
    "CHANGELOG.md", "VERSION", "requirements.txt", "settings.example.json",
    "app.py", "dashboard_core.py", "codex_activity.py", "SensorBridge.cs",
    "launch.ps1", "stop.ps1", "Start Dashboard.cmd", "Stop Dashboard.cmd",
    "Enable CPU Temperature.cmd", "Create Desktop Shortcuts.cmd",
    "assets/start.ico", "assets/stop.ico", "docs/display-example.png",
    "tools/fetch_resources.py", "tools/enable_cpu.ps1", "tools/start_sensor.ps1",
    "tools/create_icons.py", "tools/create_shortcuts.ps1", "tools/render_example.py",
    "tools/build_release.py", "tools/check_release.py",
    "tests/test_core.py", "tests/test_settings.py", "tests/test_activity.py", "tests/test_release.py",
    ".github/workflows/ci.yml", ".github/workflows/release.yml",
)


def build(output=None):
    version = (ROOT / "VERSION").read_text().strip()
    name = "nautilus-dashboard-" + version
    output = pathlib.Path(output) if output else ROOT / "dist" / (name + "-source.zip")
    output.parent.mkdir(parents=True,exist_ok=True)
    for filename in FILES:
        if not (ROOT / filename).is_file():
            raise FileNotFoundError(filename)
    with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in FILES:
            archive.write(ROOT / filename,arcname=name + "/" + filename)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n",encoding="ascii")
    print("Created",output)
    print("SHA-256:",digest)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    build(parser.parse_args().output)
