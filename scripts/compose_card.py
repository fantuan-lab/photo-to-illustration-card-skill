#!/usr/bin/env python3
"""Deterministically compose a photo and an illustration into a 3:4 card."""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import tempfile
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat
except ImportError as exc:  # Keep --help usable when Pillow is not installed.
    Image = ImageDraw = ImageFont = ImageOps = ImageStat = None  # type: ignore[assignment]
    PILLOW_IMPORT_ERROR: Exception | None = exc
else:
    PILLOW_IMPORT_ERROR = None


VERSION = 1
DEFAULT_WIDTH = 1536
DEFAULT_HEIGHT = 2048
PAPER_RGB = (246, 242, 231)
INK_RGB = (31, 34, 32)
LAYOUT_RATIOS = {"balanced": 0.50, "postcard": 0.70}
SUPPORTED_OUTPUTS = {
    ".png": ("PNG", {}),
    ".jpg": ("JPEG", {"quality": 95, "subsampling": 0, "optimize": True}),
    ".jpeg": ("JPEG", {"quality": 95, "subsampling": 0, "optimize": True}),
    ".webp": ("WEBP", {"quality": 95, "method": 6}),
}


class ComposeError(RuntimeError):
    """A user-actionable composition failure."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Combine an EXIF-corrected source photo (top) and an illustration "
            "(bottom) into a deterministic 3:4 card. A JSON sidecar is written "
            "to <out>.json, for example card.png.json."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--photo", required=True, help="Source photo; it is never overwritten")
    parser.add_argument(
        "--illustration",
        required=True,
        help="Hand-drawn PNG: transparent cutout or complete illustrated panel",
    )
    parser.add_argument(
        "--illustration-mode",
        choices=("auto", "cutout", "panel"),
        default="auto",
        help=(
            "auto uses cutout only for a real varying alpha channel; opaque/RGB images "
            "are safely treated as complete panels (no automatic background removal)"
        ),
    )
    parser.add_argument(
        "--illustration-fit",
        choices=("cover", "contain"),
        default="cover",
        help="How a complete illustration panel fills the lower partition",
    )
    parser.add_argument("--out", required=True, help="Output card (.png, .jpg, .jpeg, or .webp)")
    parser.add_argument(
        "--sidecar",
        help="Metadata path; default is <out>.json (for example card.png.json)",
    )
    parser.add_argument("--layout", choices=sorted(LAYOUT_RATIOS), default="balanced")
    parser.add_argument("--title", default="", help="Main lower-panel title")
    parser.add_argument("--subtitle", default="", help="Secondary line below the title")
    parser.add_argument("--caption", default="", help="Small caption at the lower edge")
    parser.add_argument("--callout-left", default="", help="Short callout at left of the drawing")
    parser.add_argument("--callout-right", default="", help="Short callout at right of the drawing")
    parser.add_argument(
        "--callout-left-y",
        type=unit_float,
        default=0.5,
        metavar="0..1",
        help="Vertical center position within the left callout's usable content column",
    )
    parser.add_argument(
        "--callout-right-y",
        type=unit_float,
        default=0.5,
        metavar="0..1",
        help="Vertical center position within the right callout's usable content column",
    )
    parser.add_argument(
        "--accent",
        default="auto",
        help="Accent source: auto extracts a photo color, or pass #RRGGBB",
    )
    parser.add_argument(
        "--focus-x", type=unit_float, default=0.5, metavar="0..1", help="Horizontal crop focus"
    )
    parser.add_argument(
        "--focus-y", type=unit_float, default=0.5, metavar="0..1", help="Vertical crop focus"
    )
    parser.add_argument(
        "--crop",
        choices=("cover", "contain"),
        default="cover",
        help="cover safely crops around focus; contain is the no-crop fallback",
    )
    parser.add_argument("--seed", type=int, default=20260830, help="Paper/blob random seed")
    parser.add_argument("--font", help="Optional .ttf/.otf/.ttc font path")
    parser.add_argument("--width", type=positive_int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=positive_int, default=DEFAULT_HEIGHT)
    parser.add_argument("--force", action="store_true", help="Replace existing output and sidecar")
    return parser


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def unit_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number from 0 to 1") from exc
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1 inclusive")
    return parsed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_oriented(path: Path, label: str) -> Any:
    try:
        with Image.open(path) as opened:
            opened.seek(0)
            oriented = ImageOps.exif_transpose(opened)
            oriented.load()
            return oriented.copy()
    except (OSError, ValueError) as exc:
        raise ComposeError(f"cannot open {label} '{path}': {exc}") from exc


def parse_hex_color(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", value.strip())
    if not match:
        raise ComposeError("--accent must be 'auto' or a six-digit color such as #6B8F71")
    raw = match.group(1)
    return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def hex_color(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def extract_accent(photo: Any) -> tuple[int, int, int]:
    """Choose a repeatable, useful palette color without changing the photo."""
    sample = photo.convert("RGB")
    sample.thumbnail((160, 160), Image.Resampling.LANCZOS)
    quantized = sample.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    candidates: list[tuple[float, tuple[int, int, int]]] = []
    for count, palette_index in quantized.getcolors(maxcolors=256) or []:
        offset = int(palette_index) * 3
        if offset + 2 >= len(palette):
            continue
        rgb = tuple(int(channel) for channel in palette[offset : offset + 3])
        r, g, b = (channel / 255.0 for channel in rgb)
        hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
        del hue
        # Prefer common mid-tones with some chroma; retain a neutral fallback.
        usable = 0.20 <= lightness <= 0.86
        score = float(count) * (0.35 + saturation) * (1.0 if usable else 0.18)
        candidates.append((score, rgb))
    if not candidates:
        return (115, 135, 116)
    return max(candidates, key=lambda item: item[0])[1]


def muted_accent(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = (channel / 255.0 for channel in rgb)
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    saturation = min(0.34, max(0.16, saturation * 0.48))
    lightness = min(0.79, max(0.66, lightness + 0.14))
    return tuple(round(channel * 255) for channel in colorsys.hls_to_rgb(hue, lightness, saturation))  # type: ignore[return-value]


def make_paper(width: int, height: int, seed: int) -> Any:
    """Build inexpensive deterministic low-frequency paper grain."""
    rng = random.Random(seed ^ 0x50415045)
    grid_width = max(24, width // 12)
    grid_height = max(16, height // 12)
    pixels: list[tuple[int, int, int]] = []
    for _ in range(grid_width * grid_height):
        delta = rng.randint(-7, 7)
        pixels.append(tuple(max(0, min(255, base + delta)) for base in PAPER_RGB))
    grain = Image.new("RGB", (grid_width, grid_height))
    grain.putdata(pixels)
    paper = grain.resize((width, height), Image.Resampling.BILINEAR).convert("RGBA")

    fibers = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    fiber_draw = ImageDraw.Draw(fibers)
    fiber_count = max(24, (width * height) // 42000)
    for _ in range(fiber_count):
        x = rng.randrange(width)
        y = rng.randrange(height)
        length = rng.randint(max(3, width // 240), max(5, width // 90))
        color = (130, 116, 91, rng.randint(5, 13))
        fiber_draw.line((x, y, min(width - 1, x + length), y + rng.choice((-1, 0, 1))), fill=color)
    return Image.alpha_composite(paper, fibers).convert("RGB")


def place_photo(
    photo: Any,
    target_size: tuple[int, int],
    crop_mode: str,
    focus_x: float,
    focus_y: float,
    fill: tuple[int, int, int],
) -> tuple[Any, dict[str, Any]]:
    source = photo.convert("RGBA")
    source_width, source_height = source.size
    target_width, target_height = target_size
    if source_width < 1 or source_height < 1:
        raise ComposeError("source photo has invalid dimensions")

    if crop_mode == "contain":
        scale = min(target_width / source_width, target_height / source_height)
        resized_width = max(1, round(source_width * scale))
        resized_height = max(1, round(source_height * scale))
        resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
        x = round((target_width - resized_width) * focus_x)
        y = round((target_height - resized_height) * focus_y)
        panel = Image.new("RGBA", target_size, (*fill, 255))
        panel.alpha_composite(resized, (x, y))
        metadata = {
            "mode": "contain",
            "source_crop": [0, 0, source_width, source_height],
            "resized_size": [resized_width, resized_height],
            "paste_xy": [x, y],
            "fill": hex_color(fill),
        }
        return panel.convert("RGB"), metadata

    target_ratio = target_width / target_height
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        crop_height = source_height
        crop_width = max(1, min(source_width, round(source_height * target_ratio)))
        center_x = focus_x * source_width
        left = round(center_x - crop_width / 2)
        left = max(0, min(source_width - crop_width, left))
        top = 0
    else:
        crop_width = source_width
        crop_height = max(1, min(source_height, round(source_width / target_ratio)))
        center_y = focus_y * source_height
        top = round(center_y - crop_height / 2)
        top = max(0, min(source_height - crop_height, top))
        left = 0
    crop_box = (left, top, left + crop_width, top + crop_height)
    cropped = source.crop(crop_box)
    resized = cropped.resize(target_size, Image.Resampling.LANCZOS)
    panel = Image.new("RGBA", target_size, (*fill, 255))
    panel.alpha_composite(resized)
    metadata = {
        "mode": "cover",
        "source_crop": list(crop_box),
        "resized_size": [target_width, target_height],
        "paste_xy": [0, 0],
        "focus": [focus_x, focus_y],
    }
    return panel.convert("RGB"), metadata


def resolve_font(requested: str | None, text: str = "") -> Path:
    if requested:
        candidate = Path(requested).expanduser()
        if not candidate.is_file():
            raise ComposeError(f"font does not exist: {candidate}")
        try:
            ImageFont.truetype(str(candidate), 32)
        except OSError as exc:
            raise ComposeError(f"font cannot be loaded: {candidate}: {exc}") from exc
        return candidate.resolve()

    visible = "".join(char for char in text if not char.isspace())
    ascii_only = bool(visible) and all(ord(char) < 128 for char in visible)
    handwriting_candidates = (
        "/System/Library/Fonts/MarkerFelt.ttc",
        "/System/Library/Fonts/Noteworthy.ttc",
        "/System/Library/Fonts/Supplemental/Chalkboard.ttc",
        "/System/Library/Fonts/Supplemental/ChalkboardSE.ttc",
        "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
    )
    cjk_candidates = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    candidates = handwriting_candidates + cjk_candidates if ascii_only else cjk_candidates
    for path_string in candidates:
        candidate = Path(path_string)
        if candidate.is_file():
            try:
                ImageFont.truetype(str(candidate), 32)
            except OSError:
                continue
            return candidate.resolve()
    raise ComposeError("no usable TrueType font found; pass one explicitly with --font")


def text_width(draw: Any, value: str, font: Any) -> int:
    box = draw.textbbox((0, 0), value or " ", font=font)
    return box[2] - box[0]


def split_long_token(draw: Any, token: str, font: Any, max_width: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for char in token:
        candidate = current + char
        if current and text_width(draw, candidate, font) > max_width:
            pieces.append(current)
            current = char
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces or [""]


def wrap_text(draw: Any, value: str, font: Any, max_width: int) -> str:
    output_lines: list[str] = []
    for paragraph in value.splitlines() or [""]:
        # Keep English words together, while letting CJK and punctuation wrap naturally.
        tokens = re.findall(r"[A-Za-z0-9]+(?:['_.:/-][A-Za-z0-9]+)*|\s+|.", paragraph)
        line = ""
        for token in tokens:
            if token.isspace():
                token = " "
            if text_width(draw, token, font) > max_width:
                token_parts = split_long_token(draw, token, font, max_width)
            else:
                token_parts = [token]
            for part in token_parts:
                candidate = line + part
                if line and text_width(draw, candidate, font) > max_width:
                    output_lines.append(line.rstrip())
                    line = part.lstrip()
                else:
                    line = candidate
        output_lines.append(line.rstrip())
    return "\n".join(output_lines)


def fit_text(
    draw: Any,
    value: str,
    font_path: Path,
    max_width: int,
    max_height: int,
    initial_size: int,
    min_size: int,
    max_lines: int,
) -> dict[str, Any]:
    for size in range(initial_size, min_size - 1, -2):
        font = ImageFont.truetype(str(font_path), size)
        wrapped = wrap_text(draw, value, font, max_width)
        spacing = max(2, round(size * 0.24))
        box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=spacing, align="left")
        width = box[2] - box[0]
        height = box[3] - box[1]
        lines = wrapped.count("\n") + 1
        if width <= max_width and height <= max_height and lines <= max_lines:
            return {
                "font": font,
                "font_size": size,
                "wrapped": wrapped,
                "raw_bbox": box,
                "width": width,
                "height": height,
                "spacing": spacing,
                "lines": lines,
            }
    raise ComposeError(
        f"text is too long for its safe area: {value!r}; shorten it or increase output size"
    )


def position_text(layout: dict[str, Any], left: int, top: int) -> tuple[int, int, list[int]]:
    raw_left, raw_top, raw_right, raw_bottom = layout["raw_bbox"]
    origin_x = left - raw_left
    origin_y = top - raw_top
    bbox = [left, top, left + raw_right - raw_left, top + raw_bottom - raw_top]
    return origin_x, origin_y, bbox


def text_record(
    name: str,
    requested: str,
    layout: dict[str, Any],
    bbox: list[int],
    safe_box: list[int],
    origin: tuple[int, int],
    font_path: Path,
) -> dict[str, Any]:
    return {
        "name": name,
        "text": requested,
        "rendered_text": layout["wrapped"],
        "font_size": layout["font_size"],
        "font_path": str(font_path),
        "spacing": layout["spacing"],
        "line_count": layout["lines"],
        "origin": list(origin),
        "bbox": bbox,
        "safe_box": safe_box,
    }


def fit_illustration(illustration: Any, max_width: int, max_height: int) -> tuple[Any, dict[str, Any]]:
    rgba = illustration.convert("RGBA")
    # Ignore near-invisible antialias/noise pixels for geometry only; alpha itself is preserved.
    geometry_mask = rgba.getchannel("A").point(lambda value: 255 if value >= 16 else 0)
    alpha_bbox = geometry_mask.getbbox()
    if alpha_bbox is None:
        raise ComposeError("illustration is fully transparent")
    trimmed = rgba.crop(alpha_bbox)
    source_width, source_height = trimmed.size
    scale = min(max_width / source_width, max_height / source_height)
    if not math.isfinite(scale) or scale <= 0:
        raise ComposeError("not enough lower-panel space for the illustration")
    width = max(1, round(source_width * scale))
    height = max(1, round(source_height * scale))
    resized = trimmed.resize((width, height), Image.Resampling.LANCZOS)
    return resized, {"source_trim": list(alpha_bbox), "scale": scale, "size": [width, height]}


def illustration_alpha_profile(
    illustration: Any,
) -> tuple[list[int], bool, dict[str, Any]]:
    alpha = illustration.convert("RGBA").getchannel("A")
    minimum, maximum = alpha.getextrema()
    histogram = alpha.histogram()
    total = max(1, alpha.width * alpha.height)
    transparent_pixels = sum(histogram[:9])
    opaque_pixels = sum(histogram[247:])
    transparent_fraction = transparent_pixels / total
    opaque_fraction = opaque_pixels / total
    # A cutout needs a clear background and an opaque subject. Slight global alpha variation
    # is not enough, and RGB/checkerboard pixels are never turned into transparency.
    effective = (
        minimum == 0
        and maximum == 255
        and transparent_fraction >= 0.001
        and opaque_fraction >= 0.001
    )
    profile = {
        "total_pixels": total,
        "transparent_pixels_le_8": transparent_pixels,
        "opaque_pixels_ge_247": opaque_pixels,
        "transparent_fraction_le_8": round(transparent_fraction, 8),
        "opaque_fraction_ge_247": round(opaque_fraction, 8),
    }
    return [int(minimum), int(maximum)], effective, profile


def place_illustration_panel(
    illustration: Any, target_size: tuple[int, int], fit: str
) -> tuple[Any, dict[str, Any]]:
    """Resize/crop an already-designed illustration panel; never infer or remove its background."""
    source = illustration.convert("RGBA")
    source_width, source_height = source.size
    target_width, target_height = target_size
    if source_width < 1 or source_height < 1:
        raise ComposeError("illustration panel has invalid dimensions")
    if source.getchannel("A").getextrema()[1] == 0:
        raise ComposeError("illustration panel is fully transparent")

    if fit == "contain":
        scale = min(target_width / source_width, target_height / source_height)
        resized_width = max(1, round(source_width * scale))
        resized_height = max(1, round(source_height * scale))
        rendered = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
        x = (target_width - resized_width) // 2
        y = (target_height - resized_height) // 2
        panel = Image.new("RGBA", target_size, (0, 0, 0, 0))
        panel.alpha_composite(rendered, (x, y))
        return panel, {
            "fit": "contain",
            "source_crop": [0, 0, source_width, source_height],
            "rendered_size": [resized_width, resized_height],
            "paste_xy": [x, y],
        }

    target_ratio = target_width / target_height
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        crop_height = source_height
        crop_width = max(1, min(source_width, round(source_height * target_ratio)))
        left = (source_width - crop_width) // 2
        top = 0
    else:
        crop_width = source_width
        crop_height = max(1, min(source_height, round(source_width / target_ratio)))
        left = 0
        top = (source_height - crop_height) // 2
    crop_box = [left, top, left + crop_width, top + crop_height]
    panel = source.crop(tuple(crop_box)).resize(target_size, Image.Resampling.LANCZOS)
    return panel, {
        "fit": "cover",
        "source_crop": crop_box,
        "rendered_size": [target_width, target_height],
        "paste_xy": [0, 0],
    }


def draw_blob(canvas: Any, box: list[int], color: tuple[int, int, int], seed: int) -> list[int]:
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed ^ 0x424C4F42)
    radius = max(12, min(x1 - x0, y1 - y0) // 7)
    fill = (*color, 205)
    draw.rounded_rectangle(box, radius=radius, fill=fill)
    bump_count = 8
    for _ in range(bump_count):
        diameter = rng.randint(max(16, radius), max(20, radius * 2))
        side = rng.choice(("top", "bottom", "left", "right"))
        if side in ("top", "bottom"):
            cx = rng.randint(x0, max(x0, x1))
            cy = y0 if side == "top" else y1
        else:
            cx = x0 if side == "left" else x1
            cy = rng.randint(y0, max(y0, y1))
        draw.ellipse(
            (cx - diameter // 2, cy - diameter // 2, cx + diameter // 2, cy + diameter // 2),
            fill=fill,
        )
    canvas.alpha_composite(layer)
    return box


def expanded_box(box: list[int], pad_x: int, pad_y: int, bounds: list[int]) -> list[int]:
    return [
        max(bounds[0], box[0] - pad_x),
        max(bounds[1], box[1] - pad_y),
        min(bounds[2], box[2] + pad_x),
        min(bounds[3], box[3] + pad_y),
    ]


def pixel_stats(image: Any, box: list[int]) -> dict[str, Any]:
    region = image.convert("RGB").crop(tuple(box))
    sample = region.copy()
    sample.thumbnail((256, 256), Image.Resampling.BILINEAR)
    stat = ImageStat.Stat(sample)
    grayscale = sample.convert("L")
    colors = sample.getcolors(maxcolors=256 * 256)
    return {
        "box": box,
        "mean_rgb": [round(value, 4) for value in stat.mean[:3]],
        "stddev_rgb": [round(value, 4) for value in stat.stddev[:3]],
        "extrema_rgb": [list(pair) for pair in stat.extrema[:3]],
        "entropy_l": round(grayscale.entropy(), 4),
        "sample_size": list(sample.size),
        "sample_unique_colors": len(colors) if colors is not None else 65537,
    }


def publish_staged(temporary: Path, output: Path, force: bool) -> None:
    """Publish without a check-then-replace race when --force is absent."""
    try:
        if force:
            os.replace(temporary, output)
        else:
            # The hard-link operation is atomic and fails if any entry (including a symlink)
            # already occupies the destination. Both paths are in the same directory.
            os.link(temporary, output, follow_symlinks=False)
            temporary.unlink()
    except FileExistsError as exc:
        raise ComposeError(f"refusing to overwrite file created concurrently: {output}") from exc
    except OSError as exc:
        raise ComposeError(f"cannot publish '{output}': {exc}") from exc


def save_image_atomic(image: Any, output: Path, force: bool) -> None:
    suffix = output.suffix.lower()
    if suffix not in SUPPORTED_OUTPUTS:
        raise ComposeError("--out must end in .png, .jpg, .jpeg, or .webp")
    output.parent.mkdir(parents=True, exist_ok=True)
    format_name, options = SUPPORTED_OUTPUTS[suffix]
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=suffix, dir=str(output.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        image.convert("RGB").save(temporary, format=format_name, **options)
        publish_staged(temporary, output, force)
    except OSError as exc:
        raise ComposeError(f"cannot write output image '{output}': {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def save_json_atomic(payload: dict[str, Any], output: Path, force: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        publish_staged(temporary, output, force)
    except OSError as exc:
        raise ComposeError(f"cannot write sidecar '{output}': {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def ensure_paths(photo: Path, illustration: Path, output: Path, sidecar: Path, force: bool) -> None:
    for source, label in ((photo, "photo"), (illustration, "illustration")):
        if not source.is_file():
            raise ComposeError(f"{label} does not exist or is not a file: {source}")
    sources = (photo, illustration)
    destinations = (output, sidecar)

    def absolute_case_key(path: Path) -> str:
        return os.path.abspath(os.fspath(path)).casefold()

    def same_existing(first: Path, second: Path) -> bool:
        try:
            return os.path.samefile(first, second)
        except OSError:
            return False

    for destination in destinations:
        for source in sources:
            # casefold blocks macOS case-insensitive aliases even before an entry exists.
            if absolute_case_key(destination) == absolute_case_key(source):
                raise ComposeError("output paths must not overwrite either input, even with --force")
            if os.path.lexists(destination) and same_existing(destination, source):
                raise ComposeError("output paths must not alias either input, even with --force")
    if absolute_case_key(output) == absolute_case_key(sidecar):
        raise ComposeError("output image and sidecar must be different paths")
    if os.path.lexists(output) and os.path.lexists(sidecar) and same_existing(output, sidecar):
        raise ComposeError("output image and sidecar must not alias the same file")
    for destination in destinations:
        if destination.is_dir():
            raise ComposeError(f"output path is a directory: {destination}")
    existing = [path for path in destinations if os.path.lexists(path)]
    if existing and not force:
        joined = ", ".join(str(path) for path in existing)
        raise ComposeError(f"refusing to overwrite existing file(s): {joined}; pass --force to replace")


def compose(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.width * 4 != args.height * 3:
        raise ComposeError(
            f"output must be exactly 3:4; got {args.width}x{args.height} "
            "(try 1536x2048)"
        )
    if args.width > 16384 or args.height > 16384:
        raise ComposeError("output dimensions may not exceed 16384 pixels")

    photo_path = Path(args.photo).expanduser()
    illustration_path = Path(args.illustration).expanduser()
    output_path = Path(args.out).expanduser()
    sidecar_path = Path(args.sidecar).expanduser() if args.sidecar else Path(f"{output_path}.json")
    ensure_paths(photo_path, illustration_path, output_path, sidecar_path, args.force)

    photo_hash = sha256_file(photo_path)
    illustration_hash = sha256_file(illustration_path)
    photo = load_oriented(photo_path, "photo")
    illustration = load_oriented(illustration_path, "illustration")

    if args.accent.strip().lower() == "auto":
        accent_source = extract_accent(photo)
        accent_method = "auto"
    else:
        accent_source = parse_hex_color(args.accent)
        accent_method = "explicit"
    accent_muted = muted_accent(accent_source)
    photo_fill = tuple(max(0, channel - 18) for channel in accent_muted)

    width, height = args.width, args.height
    split_y = round(height * LAYOUT_RATIOS[args.layout])
    lower_height = height - split_y
    photo_panel, photo_meta = place_photo(
        photo,
        (width, split_y),
        args.crop,
        args.focus_x,
        args.focus_y,
        photo_fill,
    )
    canvas = Image.new("RGB", (width, height), PAPER_RGB)
    canvas.paste(photo_panel, (0, 0))
    canvas.paste(make_paper(width, lower_height, args.seed), (0, split_y))
    canvas = canvas.convert("RGBA")

    alpha_extrema, has_effective_alpha, alpha_profile = illustration_alpha_profile(illustration)
    if args.illustration_mode == "auto":
        illustration_mode = "cutout" if has_effective_alpha else "panel"
    else:
        illustration_mode = args.illustration_mode
    if illustration_mode == "cutout" and not has_effective_alpha:
        raise ComposeError(
            "--illustration-mode cutout requires a genuinely transparent background. "
            "This image has no useful varying alpha channel; use auto/panel or generate a real "
            "transparent PNG. The composer will not attempt automatic background removal."
        )
    panel_meta: dict[str, Any] | None = None
    if illustration_mode == "panel":
        panel_rendered, panel_meta = place_illustration_panel(
            illustration, (width, lower_height), args.illustration_fit
        )
        canvas.alpha_composite(panel_rendered, (0, split_y))

    measure_draw = ImageDraw.Draw(canvas)
    margin_x = max(18, round(width * 0.055))
    margin_y = max(14, round(lower_height * 0.045))
    gap = max(8, round(lower_height * 0.018))
    safe_lower = [margin_x, split_y + margin_y, width - margin_x, height - margin_y]
    available_width = safe_lower[2] - safe_lower[0]
    text_layouts: dict[str, dict[str, Any]] = {}
    text_records: dict[str, dict[str, Any]] = {}
    resolved_fonts: dict[str, str] = {}

    cursor_y = safe_lower[1]
    if args.title:
        font_path = resolve_font(args.font, args.title)
        resolved_fonts["title"] = str(font_path)
        allocation = max(32, round(lower_height * 0.185))
        layout = fit_text(
            measure_draw,
            args.title,
            font_path,
            available_width,
            allocation,
            max(30, round(width * 0.047)),
            max(16, round(width * 0.022)),
            2,
        )
        origin_x, origin_y, bbox = position_text(layout, safe_lower[0], cursor_y)
        safe_box = [safe_lower[0], cursor_y, safe_lower[2], cursor_y + allocation]
        text_layouts["title"] = {**layout, "origin": (origin_x, origin_y)}
        text_records["title"] = text_record(
            "title", args.title, layout, bbox, safe_box, (origin_x, origin_y), font_path
        )
        cursor_y = bbox[3] + gap

    if args.subtitle:
        font_path = resolve_font(args.font, args.subtitle)
        resolved_fonts["subtitle"] = str(font_path)
        allocation = max(24, round(lower_height * 0.105))
        layout = fit_text(
            measure_draw,
            args.subtitle,
            font_path,
            available_width,
            allocation,
            max(22, round(width * 0.025)),
            max(14, round(width * 0.014)),
            2,
        )
        origin_x, origin_y, bbox = position_text(layout, safe_lower[0], cursor_y)
        safe_box = [safe_lower[0], cursor_y, safe_lower[2], cursor_y + allocation]
        text_layouts["subtitle"] = {**layout, "origin": (origin_x, origin_y)}
        text_records["subtitle"] = text_record(
            "subtitle", args.subtitle, layout, bbox, safe_box, (origin_x, origin_y), font_path
        )
        cursor_y = bbox[3] + gap

    caption_top_limit = safe_lower[3]
    if args.caption:
        font_path = resolve_font(args.font, args.caption)
        resolved_fonts["caption"] = str(font_path)
        allocation = max(24, round(lower_height * 0.13))
        layout = fit_text(
            measure_draw,
            args.caption,
            font_path,
            available_width,
            allocation,
            max(18, round(width * 0.020)),
            max(12, round(width * 0.012)),
            2,
        )
        caption_top = safe_lower[3] - layout["height"]
        origin_x, origin_y, bbox = position_text(layout, safe_lower[0], caption_top)
        safe_box = [safe_lower[0], safe_lower[3] - allocation, safe_lower[2], safe_lower[3]]
        text_layouts["caption"] = {**layout, "origin": (origin_x, origin_y)}
        text_records["caption"] = text_record(
            "caption", args.caption, layout, bbox, safe_box, (origin_x, origin_y), font_path
        )
        caption_top_limit = safe_box[1] - gap

    content_top = cursor_y
    content_bottom = caption_top_limit
    if content_bottom - content_top < max(40, round(lower_height * 0.20)):
        raise ComposeError("title/subtitle/caption leave too little room for the illustration")

    has_callouts = bool(args.callout_left or args.callout_right)
    blob_box: list[int] | None = None
    if illustration_mode == "cutout":
        illustration_max_width = round(width * (0.56 if has_callouts else 0.80))
        illustration_max_height = max(1, round((content_bottom - content_top) * 0.91))
        illustration_rendered, illustration_meta = fit_illustration(
            illustration, illustration_max_width, illustration_max_height
        )
        illustration_width, illustration_height = illustration_rendered.size
        illustration_x = (width - illustration_width) // 2
        illustration_y = content_top + (content_bottom - content_top - illustration_height) // 2
        illustration_box = [
            illustration_x,
            illustration_y,
            illustration_x + illustration_width,
            illustration_y + illustration_height,
        ]
        illustration_meta["destination_box"] = illustration_box
        blob_bounds = [margin_x, content_top, width - margin_x, content_bottom]
        blob_box = expanded_box(
            illustration_box,
            max(16, round(width * 0.065)),
            max(12, round(lower_height * 0.035)),
            blob_bounds,
        )
        draw_blob(canvas, blob_box, accent_muted, args.seed)
        canvas.alpha_composite(illustration_rendered, (illustration_x, illustration_y))
    else:
        illustration_box = [0, split_y, width, height]
        illustration_meta = {
            **(panel_meta or {}),
            "size": [width, lower_height],
            "destination_box": illustration_box,
        }

    # Callouts use narrow side columns so they do not cover the subject.
    callout_pills: dict[str, list[int]] = {}
    for name, requested, side in (
        ("callout_left", args.callout_left, "left"),
        ("callout_right", args.callout_right, "right"),
    ):
        if not requested:
            continue
        font_path = resolve_font(args.font, requested)
        resolved_fonts[name] = str(font_path)
        if side == "left":
            column = [safe_lower[0], content_top, round(width * 0.285), content_bottom]
        else:
            column = [round(width * 0.715), content_top, safe_lower[2], content_bottom]
        pill_pad_x = max(8, round(width * 0.012))
        pill_pad_y = max(5, round(width * 0.007))
        max_text_width = max(20, column[2] - column[0] - 2 * pill_pad_x)
        layout = fit_text(
            measure_draw,
            requested,
            font_path,
            max_text_width,
            max(24, round((content_bottom - content_top) * 0.28)),
            max(16, round(width * 0.018)),
            max(11, round(width * 0.010)),
            2,
        )
        position_y = args.callout_left_y if side == "left" else args.callout_right_y
        usable_top = content_top + pill_pad_y
        usable_bottom = content_bottom - pill_pad_y
        vertical_travel = max(0, usable_bottom - usable_top - layout["height"])
        # int() floors positive values; at 0.5 this is exactly the previous centered behavior.
        text_top = usable_top + int(vertical_travel * position_y)
        text_left = column[0] + pill_pad_x
        origin_x, origin_y, bbox = position_text(layout, text_left, text_top)
        safe_box = [
            column[0] + pill_pad_x,
            column[1] + pill_pad_y,
            column[2] - pill_pad_x,
            column[3] - pill_pad_y,
        ]
        pill = [
            bbox[0] - pill_pad_x,
            bbox[1] - pill_pad_y,
            bbox[2] + pill_pad_x,
            bbox[3] + pill_pad_y,
        ]
        text_layouts[name] = {**layout, "origin": (origin_x, origin_y)}
        record = text_record(
            name, requested, layout, bbox, safe_box, (origin_x, origin_y), font_path
        )
        record["position_y_requested"] = position_y
        record["actual_center_y"] = (bbox[1] + bbox[3]) / 2
        record["actual_center_ratio"] = round(
            ((bbox[1] + bbox[3]) / 2 - content_top) / (content_bottom - content_top), 8
        )
        record["container_bbox"] = pill
        text_records[name] = record
        callout_pills[name] = pill

    # A complete panel may be visually busy. Add fixed translucent paper bands behind its text.
    if illustration_mode == "panel" and text_records:
        backdrop_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        backdrop_draw = ImageDraw.Draw(backdrop_layer)
        panel_text_groups: list[list[int]] = []
        header_boxes = [
            text_records[name]["bbox"]
            for name in ("title", "subtitle")
            if name in text_records
        ]
        if header_boxes:
            panel_text_groups.append(
                [
                    min(box[0] for box in header_boxes),
                    min(box[1] for box in header_boxes),
                    max(box[2] for box in header_boxes),
                    max(box[3] for box in header_boxes),
                ]
            )
        if "caption" in text_records:
            panel_text_groups.append(text_records["caption"]["bbox"])
        for text_box in panel_text_groups:
            backdrop_box = expanded_box(
                text_box,
                max(10, round(width * 0.018)),
                max(7, round(width * 0.010)),
                [0, split_y, width, height],
            )
            backdrop_draw.rounded_rectangle(
                backdrop_box,
                radius=max(10, round(width * 0.014)),
                fill=(*PAPER_RGB, 222),
            )
        canvas.alpha_composite(backdrop_layer)

    final_draw = ImageDraw.Draw(canvas)
    for pill in callout_pills.values():
        final_draw.rounded_rectangle(
            pill,
            radius=max(8, round(width * 0.012)),
            fill=(250, 248, 240, 224),
            outline=(*accent_source, 135),
            width=max(1, round(width / 768)),
        )
    for name in ("title", "subtitle", "caption", "callout_left", "callout_right"):
        layout = text_layouts.get(name)
        if not layout:
            continue
        fill = INK_RGB
        final_draw.multiline_text(
            layout["origin"],
            layout["wrapped"],
            font=layout["font"],
            fill=(*fill, 255),
            spacing=layout["spacing"],
            align="left",
        )

    output_rgb = canvas.convert("RGB")
    save_image_atomic(output_rgb, output_path, args.force)
    output_hash = sha256_file(output_path)
    try:
        with Image.open(output_path) as stored:
            stored.load()
            stored_rgb = stored.convert("RGB")
    except OSError as exc:
        raise ComposeError(f"saved output cannot be reopened: {exc}") from exc

    photo_box = [0, 0, width, split_y]
    lower_box = [0, split_y, width, height]
    paper_patch_size_x = max(4, round(width * 0.025))
    paper_patch_size_y = max(4, round(lower_height * 0.025))
    paper_patches = [
        [0, split_y, paper_patch_size_x, split_y + paper_patch_size_y],
        [width - paper_patch_size_x, split_y, width, split_y + paper_patch_size_y],
        [0, height - paper_patch_size_y, paper_patch_size_x, height],
        [width - paper_patch_size_x, height - paper_patch_size_y, width, height],
    ]
    region_stats = {
        "photo": pixel_stats(stored_rgb, photo_box),
        "lower": pixel_stats(stored_rgb, lower_box),
    }

    metadata: dict[str, Any] = {
        "schema": "photo-to-illustration-card/v1",
        "composer_version": VERSION,
        "output": {
            "path": str(output_path.resolve()),
            "sha256": output_hash,
            "width": width,
            "height": height,
            "mode": stored_rgb.mode,
            "aspect_ratio": "3:4",
        },
        "inputs": {
            "photo": {
                "path": str(photo_path.resolve()),
                "sha256": photo_hash,
                "oriented_size": list(photo.size),
                "original_mode": photo.mode,
            },
            "illustration": {
                "path": str(illustration_path.resolve()),
                "sha256": illustration_hash,
                "oriented_size": list(illustration.size),
                "original_mode": illustration.mode,
            },
        },
        "parameters": {
            "layout": args.layout,
            "layout_photo_ratio": LAYOUT_RATIOS[args.layout],
            "crop": args.crop,
            "focus_x": args.focus_x,
            "focus_y": args.focus_y,
            "seed": args.seed,
            "accent_request": args.accent,
            "illustration_mode_request": args.illustration_mode,
            "illustration_fit": args.illustration_fit,
            "callout_left_y": args.callout_left_y,
            "callout_right_y": args.callout_right_y,
            "font_request": args.font,
            "fonts_resolved": resolved_fonts,
            "text": {
                "title": args.title,
                "subtitle": args.subtitle,
                "caption": args.caption,
                "callout_left": args.callout_left,
                "callout_right": args.callout_right,
            },
        },
        "palette": {
            "accent_method": accent_method,
            "accent_source": hex_color(accent_source),
            "accent_muted": hex_color(accent_muted),
            "paper": hex_color(PAPER_RGB),
            "ink": hex_color(INK_RGB),
        },
        "partition": {
            "split_y": split_y,
            "photo_ratio_actual": split_y / height,
            "photo": {"box": photo_box, **photo_meta},
            "lower": {"box": lower_box, "paper_texture_seed": args.seed},
        },
        "illustration": {
            **illustration_meta,
            "mode_requested": args.illustration_mode,
            "mode_actual": illustration_mode,
            "alpha_extrema": alpha_extrema,
            "alpha_profile": alpha_profile,
            "has_effective_alpha": has_effective_alpha,
            "background_removal_attempted": False,
        },
        "accent_blob": {
            "applied": blob_box is not None,
            "box": blob_box,
            "color": hex_color(accent_muted),
        },
        "typography": {
            "global_safe_box": safe_lower,
            "callout_positions_requested": {
                "left": args.callout_left_y,
                "right": args.callout_right_y,
            },
            "items": text_records,
        },
        "validation": {
            "region_stats": region_stats,
            "paper_sample_boxes": paper_patches,
        },
    }
    try:
        save_json_atomic(metadata, sidecar_path, args.force)
    except ComposeError:
        # With no --force this invocation created the image, so remove it if sidecar commit
        # fails. Hash-check first to avoid deleting a concurrently replaced destination.
        if not args.force and output_path.is_file():
            try:
                if sha256_file(output_path) == output_hash:
                    output_path.unlink()
            except OSError:
                pass
        raise
    return output_path, sidecar_path


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if PILLOW_IMPORT_ERROR is not None:
        print(
            "compose_card.py: Pillow is required. Install it with "
            "'python3 -m pip install Pillow'.",
            file=sys.stderr,
        )
        return 2
    try:
        output, sidecar = compose(args)
    except ComposeError as exc:
        print(f"compose_card.py: error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"ok": True, "output": str(output), "sidecar": str(sidecar)},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
