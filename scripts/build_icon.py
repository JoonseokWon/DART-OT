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
    tile = Image.new("RGBA", (SIZE, SIZE), (5, 45, 43, 255))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((36, 36, 988, 988), radius=190, fill=255)
    canvas.alpha_composite(Image.composite(tile, Image.new("RGBA", (SIZE, SIZE)), mask))
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((72, 72, 952, 952), radius=150, outline=(94, 234, 212, 255), width=22)
    monogram = font(355)
    draw.text((130, 165), "D", font=monogram, fill=(245, 255, 253, 255))
    draw.text((510, 165), "O", font=monogram, fill=(94, 234, 212, 255))

    # Interest-overall-test motif: calculated rate versus observed rate.
    draw.line([(170, 770), (400, 630), (610, 705), (850, 535)], fill=(245, 255, 253, 255), width=42, joint="curve")
    draw.line([(170, 815), (400, 735), (610, 650), (850, 590)], fill=(94, 234, 212, 255), width=28, joint="curve")
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
