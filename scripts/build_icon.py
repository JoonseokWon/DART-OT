from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "DART-OT.png"
ICO_PATH = ASSETS / "DART-OT.ico"
SIZE = 1024


def font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\seguisb.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    raise FileNotFoundError("A bold Windows font was not found.")


def build_icon() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (SIZE, SIZE))
    pixels = gradient.load()
    # Keep the large two-letter family resemblance of DART-QoE, while using
    # DART-OT's own emerald/amber palette so the apps remain easy to tell apart.
    top = (4, 45, 41)
    bottom = (10, 78, 67)
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        color = tuple(round(top[index] * (1 - ratio) + bottom[index] * ratio) for index in range(3)) + (255,)
        for x in range(SIZE):
            pixels[x, y] = color

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((28, 28, 996, 996), radius=190, fill=255)
    canvas.alpha_composite(Image.composite(gradient, Image.new("RGBA", (SIZE, SIZE)), mask))
    draw = ImageDraw.Draw(canvas)

    monogram = font(430)
    label = "DO"
    bbox = draw.textbbox((0, 0), label, font=monogram)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (SIZE - text_width) // 2
    y = (SIZE - text_height) // 2 - bbox[1] - 5
    draw.text((x, y), "D", font=monogram, fill=(248, 252, 251, 255))
    d_width = draw.textlength("D", font=monogram)
    draw.text((round(x + d_width), y), "O", font=monogram, fill=(255, 184, 77, 255))
    return canvas


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = build_icon()
    image.save(PNG_PATH, optimize=True)
    image.save(ICO_PATH, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(PNG_PATH)
    print(ICO_PATH)


if __name__ == "__main__":
    main()
