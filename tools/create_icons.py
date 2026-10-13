"""Draw simple Start/Stop shortcut icons; no external assets are needed."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def main():
    target = ROOT / "assets"
    target.mkdir(exist_ok=True)
    for name,color in [("start","#45d9ff"),("stop","#ff718a")]:
        image = Image.new("RGBA", (768,768), (0,0,0,0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((30,30,738,738),radius=150,fill="#101923",outline="#33465b",width=12)
        draw.ellipse((129,129,639,639),outline=color,width=34)
        if name == "start":
            draw.polygon([(307,246),(307,522),(522,384)],fill="#f2f7ff")
        else:
            draw.rounded_rectangle((273,273,495,495),radius=25,fill="#f2f7ff")
        image.resize((256,256),Image.Resampling.LANCZOS).save(target / (name+".ico"),sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print("Created Start and Stop icons in assets/.")


if __name__ == "__main__":
    main()
