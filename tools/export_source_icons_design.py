from __future__ import annotations

import re
from pathlib import Path


def _extract_balanced_block(text: str, marker: str, open_ch: str, close_ch: str) -> str:
    marker_index = text.find(marker)
    if marker_index < 0:
        raise ValueError(f"marker not found: {marker}")

    start = text.find(open_ch, marker_index)
    if start < 0:
        raise ValueError(f"open token not found after marker: {marker}")

    depth = 0
    in_single = False
    in_double = False
    in_backtick = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]

        if escaped:
            escaped = False
            continue

        if ch == "\\" and (in_single or in_double or in_backtick):
            escaped = True
            continue

        if not (in_double or in_backtick) and ch == "'" and not escaped:
            in_single = not in_single
            continue

        if not (in_single or in_backtick) and ch == '"' and not escaped:
            in_double = not in_double
            continue

        if not (in_single or in_double) and ch == "`" and not escaped:
            in_backtick = not in_backtick
            continue

        if in_single or in_double or in_backtick:
            continue

        if ch == open_ch:
            depth += 1
            continue

        if ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError(f"unterminated block for marker: {marker}")


def _parse_theme_source_chip_colors(theme_tokens_css: str) -> dict[str, str]:
    colors: dict[str, str] = {}
    for match in re.finditer(
        r"--(source-chip-\d+)\s*:\s*([^;]+);",
        theme_tokens_css,
    ):
        token = match.group(1).strip()
        value = match.group(2).strip()
        if token.endswith("-soft"):
            continue
        colors[token] = value
    return colors


def _parse_filter_chip_tokens(app_js: str) -> list[str]:
    raw = _extract_balanced_block(app_js, "const FILTER_CHIP_TOKENS", "[", "]")
    return [m.group(1) for m in re.finditer(r'"([^"]+)"', raw)]


def _parse_special_gradients(app_js: str) -> dict[str, str]:
    raw = _extract_balanced_block(app_js, "const SPECIAL_GRADIENT_COLORS", "{", "}")
    gradients: dict[str, str] = {}
    for match in re.finditer(r"([^\s:,{]+)\s*:\s*\"([^\"]+)\"", raw):
        key = match.group(1).strip()
        value = match.group(2).strip()
        gradients[key] = value
    return gradients


def _parse_source_icon_catalog(app_js: str) -> list[dict[str, str]]:
    raw = _extract_balanced_block(app_js, "const SOURCE_ICON_CATALOG", "[", "]")
    pattern = re.compile(
        r"name:\s*\"([^\"]+)\"\s*,\s*svg:\s*'((?:\\.|[^\\'])*?)'\s*,?",
        re.DOTALL,
    )
    items: list[dict[str, str]] = []
    for match in pattern.finditer(raw):
        name = match.group(1).strip()
        svg = match.group(2)
        svg = svg.replace("\\n", "\n")
        items.append({"name": name, "svg": svg})
    if not items:
        raise ValueError("no items parsed from SOURCE_ICON_CATALOG")
    return items


def _sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "unknown"


def _gradient_to_svg(gradient: str) -> tuple[str, str]:
    colors = re.findall(r"#(?:[0-9a-fA-F]{3,8})", gradient)
    color_a = colors[0] if len(colors) >= 1 else "#000000"
    color_b = colors[1] if len(colors) >= 2 else color_a
    angle_match = re.search(r"([0-9.]+)deg", gradient)
    angle = float(angle_match.group(1)) if angle_match else 135.0

    import math

    rad = math.radians(angle)
    dx = math.sin(rad)
    dy = -math.cos(rad)
    x1 = 0.5 - dx * 0.5
    y1 = 0.5 - dy * 0.5
    x2 = 0.5 + dx * 0.5
    y2 = 0.5 + dy * 0.5

    defs = (
        "<defs>"
        f'<linearGradient id="bg" gradientUnits="objectBoundingBox" '
        f'x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}">'
        f'<stop offset="0%" stop-color="{color_a}"/>'
        f'<stop offset="100%" stop-color="{color_b}"/>'
        "</linearGradient>"
        "</defs>"
    )
    return defs, "url(#bg)"


def _make_tile_svg(background: str, icon_svg: str, *, size: int = 28) -> str:
    icon = icon_svg
    icon = re.sub(r'fill="black"', 'fill="#ffffff"', icon)
    icon = re.sub(r'fill-opacity="0\.[0-9]+"', "", icon)
    icon = re.sub(r'fill-opacity="1"', "", icon)
    icon = re.sub(r"\s{2,}", " ", icon)
    icon = re.sub(r">\s+<", "><", icon)
    icon = re.sub(r"<\?xml[^>]*\?>", "", icon).strip()
    icon = re.sub(r"<!DOCTYPE[^>]*>", "", icon).strip()

    icon = re.sub(
        r"<svg\b",
        '<svg x="5" y="5" width="18" height="18"',
        icon,
        count=1,
    )

    defs = ""
    bg_fill = background
    if "linear-gradient" in background:
        defs, bg_fill = _gradient_to_svg(background)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">'
        f"{defs}"
        f'<rect x="0" y="0" width="{size}" height="{size}" rx="8" fill="{bg_fill}"/>'
        f"{icon}"
        "</svg>"
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    app_js_path = root / "frontend" / "js" / "app.js"
    theme_tokens_path = root / "frontend" / "css" / "theme-tokens.css"
    out_dir = root / "frontend" / "icons" / "design"

    app_js = app_js_path.read_text(encoding="utf-8")
    theme_css = theme_tokens_path.read_text(encoding="utf-8")

    chip_tokens = _parse_filter_chip_tokens(app_js)
    icons = _parse_source_icon_catalog(app_js)
    gradients = _parse_special_gradients(app_js)
    colors = _parse_theme_source_chip_colors(theme_css)

    out_dir.mkdir(parents=True, exist_ok=True)

    for index, item in enumerate(icons):
        name = item["name"]
        token_name = chip_tokens[index] if index < len(chip_tokens) else chip_tokens[0]
        background = gradients.get(name) or colors.get(token_name) or "rgb(0, 122, 255)"
        svg_text = _make_tile_svg(background, item["svg"])
        filename = f"{index + 1:02d}-{_sanitize_filename(name)}.svg"
        (out_dir / filename).write_text(svg_text, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
