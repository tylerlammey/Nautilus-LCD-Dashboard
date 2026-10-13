"""Render a reproducible README example with sample readings, not user data."""
import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from PIL import Image, ImageDraw
from dashboard_core import DEFAULT_CONFIG, render_frame


def main():
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(remaining=True,rotation=0)
    state = {
        "hardware": {"cpu_temp":65,"cpu_load":24,"gpu_temp":58,"gpu_load":95},
        "quotas": {"weekly":{"used":45,"resets_at":None},"five_hour":{"used":32,"resets_at":None}},
        "codex_activity": {"status":"active","active_sessions":1}
    }
    image = render_frame(state,config).convert("RGBA")
    mask = Image.new("L",image.size,0)
    ImageDraw.Draw(mask).ellipse((0,0,479,479),fill=255)
    image.putalpha(mask)
    directory = ROOT / "docs"
    directory.mkdir(exist_ok=True)
    image.save(directory / "display-example.png")
    print("Rendered docs/display-example.png using sample data.")


if __name__ == "__main__":
    main()
