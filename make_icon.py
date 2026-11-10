"""Draw the Jarvis app icon (assets/jarvis.ico). Run by install_app.ps1."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 512
ACCENT = (53, 200, 255)


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=112, fill=(5, 11, 18, 255))
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((150, 150, 362, 362), fill=ACCENT + (150,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(46)))
    d = ImageDraw.Draw(img)
    box = lambda r: (SIZE / 2 - r, SIZE / 2 - r, SIZE / 2 + r, SIZE / 2 + r)
    d.arc(box(188), 20, 250, fill=ACCENT + (90,), width=22)
    d.arc(box(188), 270, 350, fill=ACCENT + (200,), width=22)
    for start in (-70, 50, 170):
        d.arc(box(138), start, start + 78, fill=ACCENT + (255,), width=16)
    d.ellipse(box(96), outline=ACCENT + (120,), width=4)
    d.ellipse(box(46), fill=(220, 246, 255, 255))
    return img


if __name__ == "__main__":
    out = Path(__file__).parent / "assets"
    out.mkdir(exist_ok=True)
    draw().save(out / "jarvis.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    draw().save(out / "jarvis.png")
    print(out / "jarvis.ico")
