from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    font_paths = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    font_path = next((path for path in font_paths if path.is_file()), None)
    if font_path is None:
        raise SystemExit("A Windows Chinese font is required for the OCR smoke fixture")

    image = Image.new("RGB", (1000, 260), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 64)
    draw.text((55, 80), "施工责任人：刘纪林", font=font, fill="black")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format="JPEG", quality=94)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
