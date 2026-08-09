from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    size = 512
    scale = 4
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    teal = (23, 107, 135, 255)
    yellow = (246, 190, 52, 255)
    white = (255, 255, 255, 255)
    navy = (24, 36, 45, 255)
    margin = 42 * scale
    draw.rounded_rectangle(
        (margin, margin, size * scale - margin, size * scale - margin),
        radius=82 * scale, fill=teal,
    )
    draw.arc((122 * scale, 126 * scale, 390 * scale, 390 * scale), 190, 350, fill=white, width=34 * scale)
    draw.rounded_rectangle((120 * scale, 270 * scale, 392 * scale, 316 * scale), radius=20 * scale, fill=yellow)
    draw.pieslice((156 * scale, 128 * scale, 356 * scale, 328 * scale), 180, 360, fill=yellow)
    draw.rectangle((240 * scale, 142 * scale, 272 * scale, 270 * scale), fill=white)
    draw.line((294 * scale, 356 * scale, 330 * scale, 392 * scale, 402 * scale, 324 * scale), fill=white, width=30 * scale, joint="curve")
    canvas = canvas.resize((size, size), Image.Resampling.LANCZOS)
    output = Path("assets/app.ico")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
