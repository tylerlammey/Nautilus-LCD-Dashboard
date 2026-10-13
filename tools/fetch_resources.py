"""Download dependencies into this workspace only; never change iCUE files."""
import io
import hashlib
import json
import pathlib
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPENER = urllib.request.build_opener()
HARDWARE_MONITOR_URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip"
HARDWARE_MONITOR_SHA256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "NautilusDashboard/1.0"})
    with OPENER.open(request, timeout=60) as response:
        return response.read()


def main():
    vendor = ROOT / "vendor"
    vendor.mkdir(exist_ok=True)
    release = HARDWARE_MONITOR_URL
    payload = download(release)
    if hashlib.sha256(payload).hexdigest() != HARDWARE_MONITOR_SHA256:
        raise ValueError("Hardware-monitor download failed its SHA-256 check")
    target = vendor / "LibreHardwareMonitor"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise ValueError("Unsafe archive path")
        archive.extractall(target)
    (vendor / "sources.json").write_text(json.dumps({"hardware_monitor": release,
        "sha256": HARDWARE_MONITOR_SHA256}, indent=2),encoding="utf-8")
    print("Downloaded and verified LibreHardwareMonitor into vendor/.")


if __name__ == "__main__":
    main()
