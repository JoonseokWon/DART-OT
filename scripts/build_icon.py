from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 1024
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\seguisb.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    raise FileNotFoundError("A bold Windows font was not found.")


def build_icon(
    label: str,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    accent: tuple[int, int, int],
) -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (SIZE, SIZE))
    pixels = gradient.load()
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
    bbox = draw.textbbox((0, 0), label, font=monogram)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (SIZE - text_width) // 2
    y = (SIZE - text_height) // 2 - bbox[1] - 5
    draw.text((x, y), label[0], font=monogram, fill=(248, 252, 251, 255))
    d_width = draw.textlength("D", font=monogram)
    draw.text((round(x + d_width), y), label[1], font=monogram, fill=accent + (255,))
    return canvas


def save_icon(name: str, image: Image.Image) -> None:
    png_path = ASSETS / f"{name}.png"
    ico_path = ASSETS / f"{name}.ico"
    image.save(png_path, optimize=True)
    image.save(ico_path, format="ICO", sizes=ICON_SIZES)
    print(png_path)
    print(ico_path)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    # DART-OT: emerald background with an amber O.
    save_icon("DART-OT", build_icon("DO", (4, 45, 41), (10, 78, 67), (255, 184, 77)))
    # Disclosure Viewer: indigo background with a lavender V.
    save_icon("DART-Disclosure-Viewer", build_icon("DV", (31, 29, 72), (62, 53, 119), (196, 181, 253)))


if __name__ == "__main__":
    main()
