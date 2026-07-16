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
    top = (5, 45, 43)
    bottom = (8, 72, 66)
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        color = tuple(round(top[index] * (1 - ratio) + bottom[index] * ratio) for index in range(3)) + (255,)
        for x in range(SIZE):
            pixels[x, y] = color

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((36, 36, 988, 988), radius=190, fill=255)
    canvas.alpha_composite(Image.composite(gradient, Image.new("RGBA", (SIZE, SIZE)), mask))
    draw = ImageDraw.Draw(canvas)

    # A strong side rail gives DART-OT a document/audit silhouette rather than
    # the chart-first silhouette used by DART-QoE.
    draw.rounded_rectangle((76, 88, 116, 936), radius=20, fill=(94, 234, 212, 255))

    monogram = font(330)
    draw.text((150, 120), "D", font=monogram, fill=(245, 255, 253, 255))
    draw.text((520, 120), "O", font=monogram, fill=(94, 234, 212, 255))

    # Overall-test motif: extracted filing rows with a separate approval badge.
    card = (160, 610, 830, 865)
    draw.rounded_rectangle(card, radius=48, fill=(232, 247, 244, 255))
    row_color = (28, 103, 94, 255)
    accent = (22, 165, 143, 255)
    for y, length in ((670, 360), (735, 455), (800, 285)):
        draw.ellipse((205, y - 14, 233, y + 14), fill=accent)
        draw.rounded_rectangle((260, y - 12, 260 + length, y + 12), radius=12, fill=row_color)

    badge_center = (810, 790)
    draw.ellipse(
        (
            badge_center[0] - 112,
            badge_center[1] - 112,
            badge_center[0] + 112,
            badge_center[1] + 112,
        ),
        fill=(94, 234, 212, 255),
        outline=(5, 45, 43, 255),
        width=18,
    )
    draw.line([(748, 790), (792, 832), (870, 738)], fill=(5, 45, 43, 255), width=30, joint="curve")
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
