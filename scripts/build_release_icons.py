#!/usr/bin/env python3
"""根据 frontend/icons/icon.png 生成发布所需的 .ico / .icns 图标。"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ICONS_DIR = ROOT / "frontend" / "icons"
SOURCE_PNG = ICONS_DIR / "icon.png"
TARGET_ICO = ICONS_DIR / "icon.ico"
TARGET_ICNS = ICONS_DIR / "icon.icns"


def normalize_square_image(image: Image.Image) -> Image.Image:
    side = max(image.width, image.height)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    offset = ((side - image.width) // 2, (side - image.height) // 2)
    canvas.paste(image, offset, image)
    return canvas


def build_macos_app_icon(image: Image.Image) -> Image.Image:
    size = 1024
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 为 macOS 应用图标提供一层白底，让 Dock / Finder 中的观感更稳定。
    background_margin = 56
    background_radius = 220
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (
            background_margin,
            background_margin + 12,
            size - background_margin,
            size - background_margin + 12,
        ),
        radius=background_radius,
        fill=(0, 0, 0, 34),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    canvas.alpha_composite(shadow)

    background = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    background_draw = ImageDraw.Draw(background)
    background_draw.rounded_rectangle(
        (
            background_margin,
            background_margin,
            size - background_margin,
            size - background_margin,
        ),
        radius=background_radius,
        fill=(255, 255, 255, 255),
    )
    canvas.alpha_composite(background)

    icon_size = 820
    overlay = image.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
    icon_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon_offset = ((size - icon_size) // 2, (size - icon_size) // 2 - 6)
    icon_layer.paste(overlay, icon_offset, overlay)
    canvas.alpha_composite(icon_layer)
    return canvas


def build_ico(image: Image.Image) -> None:
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    image.save(TARGET_ICO, format="ICO", sizes=sizes)
    print(f"[icons] 已生成 {TARGET_ICO}")


def build_icns(image: Image.Image) -> None:
    if sys.platform != "darwin":
        print("[icons] 非 macOS 环境，跳过 .icns 生成")
        return

    macos_icon = build_macos_app_icon(image)
    macos_icon.save(TARGET_ICNS)
    print(f"[icons] 已生成 {TARGET_ICNS}")


def main() -> int:
    if not SOURCE_PNG.exists():
        print(f"[icons] 找不到源图标: {SOURCE_PNG}", file=sys.stderr)
        return 1

    image = normalize_square_image(Image.open(SOURCE_PNG).convert("RGBA"))
    build_ico(image)
    build_icns(image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
