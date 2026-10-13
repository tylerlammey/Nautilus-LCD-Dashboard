"""Shared data selection, 480px renderer, and the Nautilus frame protocol."""
import io
import copy
import math
import pathlib
import re
import struct
import time

from PIL import Image, ImageDraw, ImageFont

PALETTES = {
    "electric": dict(bg="#05090f", ink="#f3f7ff", cpu="#45d9ff", gpu="#ba8cff", ai="#6de5ad"),
    "mint": dict(bg="#04100e", ink="#ecfff9", cpu="#77efbc", gpu="#82cfff", ai="#f3df91"),
    "ember": dict(bg="#100a06", ink="#fff6e9", cpu="#ffbb66", gpu="#ff796d", ai="#e7d6ad"),
    "mono": dict(bg="#050505", ink="#ffffff", cpu="#ffffff", gpu="#c7c7c7", ai="#e5e5e5"),
}
DEFAULT_CONFIG = {"theme": PALETTES["electric"].copy(), "remaining": True,
                  "rotation": 0, "brightness": 100, "lcd_enabled": True,
                  "close_icue_on_start": True,
                  "temperature_range": {"min_c": 20, "max_c": 100},
                  "codex_activity": {"enabled": True, "active_color": "#6de5ad",
                                     "idle_color": "#344052", "unknown_color": "#d6aa66",
                                     "radius": 8, "poll_interval_seconds": 2},
                  "layout": {"temperature_font_size": 54, "usage_font_size": 24,
                             "label_font_size": 24, "quota_font_size": 34,
                             "quota_label_font_size": 19, "hardware_ring_radius": 85,
                             "quota_ring_radius": 62, "hardware_ring_width": 11,
                             "quota_ring_width": 8, "show_reset_times": False}}


def merge_config(patch, base=None):
    config = copy.deepcopy(DEFAULT_CONFIG if base is None else base)
    if not isinstance(patch, dict) or set(patch) - set(DEFAULT_CONFIG):
        raise ValueError("Unknown setting")
    for key, value in patch.items():
        if isinstance(DEFAULT_CONFIG[key], dict):
            if not isinstance(value, dict) or set(value) - set(DEFAULT_CONFIG[key]):
                raise ValueError("Unknown " + key + " setting")
            config[key].update(value)
        else:
            config[key] = value
    if any(not isinstance(v, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", v)
           for v in config["theme"].values()):
        raise ValueError("Colors must be six-digit hex values, such as #45d9ff")
    for key in ("remaining", "lcd_enabled", "close_icue_on_start"):
        if type(config[key]) is not bool:
            raise ValueError(key + " must be true or false")
    if type(config["rotation"]) is not int or config["rotation"] not in (0, 90, 180, 270):
        raise ValueError("Rotation must be 0, 90, 180 or 270")
    if type(config["brightness"]) is not int or not 33 <= config["brightness"] <= 100:
        raise ValueError("Brightness must be 33–100")
    bounds = config["temperature_range"]
    if not all(valid_number(v, -20, 150) for v in bounds.values()) or bounds["min_c"] >= bounds["max_c"]:
        raise ValueError("Temperature minimum must be smaller than maximum")
    ranges = {"temperature_font_size": (32, 60), "usage_font_size": (16, 26),
              "label_font_size": (16, 26), "quota_font_size": (22, 38),
              "quota_label_font_size": (14, 22), "hardware_ring_radius": (70, 88),
              "quota_ring_radius": (45, 64), "hardware_ring_width": (5, 14),
              "quota_ring_width": (4, 12)}
    for key, (low, high) in ranges.items():
        if type(config["layout"][key]) is not int or not low <= config["layout"][key] <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}")
    if type(config["layout"]["show_reset_times"]) is not bool:
        raise ValueError("show_reset_times must be true or false")
    indicator = config["codex_activity"]
    if type(indicator["enabled"]) is not bool:
        raise ValueError("codex_activity.enabled must be true or false")
    for key in ("active_color", "idle_color", "unknown_color"):
        if not isinstance(indicator[key], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", indicator[key]):
            raise ValueError("Activity colors must be six-digit hex values")
    if type(indicator["radius"]) is not int or not 4 <= indicator["radius"] <= 12:
        raise ValueError("Activity dot radius must be 4–12")
    if not valid_number(indicator["poll_interval_seconds"], 1, 30):
        raise ValueError("Activity poll interval must be 1–30 seconds")
    return config


def temperature_percent(value, bounds):
    if value is None:
        return None
    return max(0, min(100, (value - bounds["min_c"]) * 100 / (bounds["max_c"] - bounds["min_c"])))


def valid_number(value, low, high):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def select_sensors(sensors):
    """Use CPU package/control temperature and discrete GPU core, not hotspot/memory."""
    result = dict(cpu_temp=None, cpu_load=None, gpu_temp=None, gpu_load=None,
                  cpu_name="CPU", gpu_name="GPU")
    cpu = [s for s in sensors if s.get("hardwareType") == "Cpu"]
    gpu = [s for s in sensors if s.get("hardwareType", "").startswith("Gpu")]
    names = list(dict.fromkeys(s["hardware"] for s in gpu))
    # Prefer the discrete AMD/NVIDIA card over an integrated Radeon GPU.
    chosen = next((n for n in names if any(k in n.lower() for k in ("rx ", "rtx ", "gtx ", "arc "))), names[0] if names else None)
    gpu = [s for s in gpu if s["hardware"] == chosen]
    if cpu:
        result["cpu_name"] = cpu[0]["hardware"]
    if chosen:
        result["gpu_name"] = chosen
    def pick(group, kind, preferences, low, high):
        available = [s for s in group if s.get("type") == kind and valid_number(s.get("value"), low, high)]
        for name in preferences:
            for s in available:
                if s["name"].lower() == name.lower():
                    return round(s["value"], 1)
        return None
    result["cpu_temp"] = pick(cpu, "Temperature", ["Core (Tctl/Tdie)", "CPU Package", "Core (Tdie)", "CPU (Tctl/Tdie)", "Core (Tctl)"], 1, 130)
    result["cpu_load"] = pick(cpu, "Load", ["CPU Total"], 0, 100)
    result["gpu_temp"] = pick(gpu, "Temperature", ["GPU Core", "GPU Temperature"], 1, 130)
    result["gpu_load"] = pick(gpu, "Load", ["GPU Core", "GPU Total", "D3D 3D"], 0, 100)
    return result


def select_quotas(response):
    bucket = (response.get("rateLimitsByLimitId") or {}).get("codex") or response.get("rateLimits") or {}
    windows = [bucket.get("primary"), bucket.get("secondary")]
    output = {"five_hour": None, "weekly": None}
    for window in windows:
        if not isinstance(window, dict) or not valid_number(window.get("usedPercent"), 0, 100):
            continue
        key = {300: "five_hour", 10080: "weekly"}.get(window.get("windowDurationMins"))
        if key:
            reset = window.get("resetsAt")
            output[key] = {"used": window["usedPercent"], "resets_at": reset if valid_number(reset, 0, 1e12) else None}
    return output


def image_packets(jpeg):
    if not jpeg or len(jpeg) > 1016 * 256:
        raise ValueError("LCD frame is empty or too large")
    count = (len(jpeg) + 1015) // 1016
    for index in range(count):
        chunk = jpeg[index * 1016:(index + 1) * 1016]
        packet = bytearray(1024)
        packet[0:2] = b"\x02\x05"
        packet[3] = int(index == count - 1)
        packet[4] = index
        struct.pack_into("<H", packet, 6, len(chunk))
        packet[8:8 + len(chunk)] = chunk
        yield bytes(packet)


_FONTS = {}
def font(size, bold=False):
    key = size, bold
    if key not in _FONTS:
        name = "segoeuib.ttf" if bold else "segoeui.ttf"
        path = pathlib.Path("C:/Windows/Fonts") / name
        _FONTS[key] = ImageFont.truetype(str(path), size * 2) if path.exists() else ImageFont.load_default(size=size * 2)
    return _FONTS[key]


def blend(a, b, amount):
    a, b = tuple(bytes.fromhex(a.lstrip("#"))), tuple(bytes.fromhex(b.lstrip("#")))
    return tuple(round(x * (1-amount) + y * amount) for x, y in zip(a, b))


def countdown(reset, now=None):
    if reset is None:
        return ""
    seconds = max(0, int(reset - (time.time() if now is None else now)))
    if seconds == 0:
        return "RESET DUE"
    if seconds >= 86400:
        return f"{seconds // 86400}d {seconds % 86400 // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{max(1, seconds // 60)}m"


def render_frame(state, config):
    theme = config["theme"]
    layout = config.get("layout", DEFAULT_CONFIG["layout"])
    image = Image.new("RGB", (960, 960), theme["bg"])
    draw = ImageDraw.Draw(image)
    muted = blend(theme["ink"], theme["bg"], .48)
    def text(value, x, y, size=18, color=None, bold=False):
        draw.text((x*2, y*2), str(value), fill=color or theme["ink"], font=font(size, bold), anchor="mm")
    def ring(x, y, radius, percent, color, width):
        box = ((x-radius)*2, (y-radius)*2, (x+radius)*2, (y+radius)*2)
        draw.ellipse(box, outline=blend(color, theme["bg"], .84), width=width*2)
        if percent is not None and percent > 0:
            draw.arc(box, start=-90, end=-90+percent*3.6, fill=color, width=width*2)
            r = radius-width/2
            for angle in (-math.pi/2, -math.pi/2+percent*math.tau/100):
                px, py = x+r*math.cos(angle), y+r*math.sin(angle)
                draw.ellipse(((px-width/2)*2,(py-width/2)*2,(px+width/2)*2,(py+width/2)*2),fill=color)
    indicator = config.get("codex_activity", DEFAULT_CONFIG["codex_activity"])
    if indicator["enabled"]:
        activity = state.get("codex_activity", {}).get("status", "unknown")
        color = indicator.get(activity + "_color", indicator["unknown_color"])
        radius = indicator["radius"]
        draw.ellipse(((240-radius)*2,(53-radius)*2,(240+radius)*2,(53+radius)*2),fill=color)
    hardware = state.get("hardware", {})
    for x,key,label in [(145,"cpu","CPU"),(335,"gpu","GPU")]:
        temp, load = hardware.get(key+"_temp"), hardware.get(key+"_load")
        percent = temperature_percent(temp, config.get("temperature_range", DEFAULT_CONFIG["temperature_range"]))
        ring(x,169,layout["hardware_ring_radius"],percent,theme[key],layout["hardware_ring_width"])
        text(label,x,141,layout["label_font_size"],theme[key],True)
        text(f"{temp:.0f}°" if temp is not None else "—",x,189,layout["temperature_font_size"],bold=True)
        text(f"{load:.0f}% LOAD" if load is not None else "LOAD —",x,279,layout["usage_font_size"],theme[key])
    quotas = state.get("quotas", {})
    for x,key,label in [(170,"weekly","WEEK"),(310,"five_hour","5H")]:
        quota = quotas.get(key)
        percent = quota["used"] if quota else None
        if percent is not None and config["remaining"]:
            percent = 100-percent
        ring(x,365,layout["quota_ring_radius"],percent,theme["ai"],layout["quota_ring_width"])
        # Keep stale data identifiable without a header or footer.
        label += "*" if state.get("quota_stale") else ""
        text(label,x,343,layout["quota_label_font_size"],muted)
        text(f"{percent:.0f}%" if percent is not None else "—",x,383,layout["quota_font_size"],theme["ai"],True)
        if quota and layout["show_reset_times"]:
            text(countdown(quota.get("resets_at")),x,445,17,muted)
    return image.resize((480,480),Image.Resampling.LANCZOS)


def encode_image(image, format="PNG"):
    output = io.BytesIO()
    image.save(output,format=format, **({"quality":88} if format == "JPEG" else {}))
    return output.getvalue()
