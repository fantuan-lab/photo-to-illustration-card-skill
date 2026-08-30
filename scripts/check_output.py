#!/usr/bin/env python3
"""Validate a card and the JSON geometry/hash sidecar from compose_card.py."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont, ImageStat
except ImportError as exc:  # Keep --help usable without Pillow.
    Image = ImageDraw = ImageFont = ImageStat = None  # type: ignore[assignment]
    PILLOW_IMPORT_ERROR: Exception | None = exc
else:
    PILLOW_IMPORT_ERROR = None


DEFAULT_WIDTH = 1536
DEFAULT_HEIGHT = 2048


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check that a generated card opens, is exactly 3:4, matches its dimensions/hash, "
            "has valid photo/lower partitions, and keeps every text bbox inside its recorded "
            "safe area. The default sidecar path is <image>.json (card.png.json)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("image_positional", nargs="?", help="Card image (alternative to --image)")
    parser.add_argument("--image", help="Card image to validate")
    parser.add_argument("--sidecar", help="Sidecar path; default is <image>.json")
    parser.add_argument("--expected-width", type=positive_int, help="Override expected width")
    parser.add_argument("--expected-height", type=positive_int, help="Override expected height")
    parser.add_argument(
        "--allow-missing-sidecar",
        action="store_true",
        help="Run image-only checks if the JSON sidecar is absent",
    )
    return parser


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_box(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(coordinate, int) and not isinstance(coordinate, bool) for coordinate in value)
        and value[0] < value[2]
        and value[1] < value[3]
    )


def box_inside(inner: list[int], outer: list[int]) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def pixel_stats(image: Any, box: list[int]) -> dict[str, Any]:
    region = image.convert("RGB").crop(tuple(box))
    sample = region.copy()
    sample.thumbnail((256, 256), Image.Resampling.BILINEAR)
    stat = ImageStat.Stat(sample)
    colors = sample.getcolors(maxcolors=256 * 256)
    return {
        "mean_rgb": [round(value, 4) for value in stat.mean[:3]],
        "stddev_rgb": [round(value, 4) for value in stat.stddev[:3]],
        "entropy_l": round(sample.convert("L").entropy(), 4),
        "sample_unique_colors": len(colors) if colors is not None else 65537,
    }


def compare_stats(
    name: str, recorded: dict[str, Any], actual: dict[str, Any], errors: list[str]
) -> None:
    for key in ("mean_rgb", "stddev_rgb"):
        expected_values = recorded.get(key)
        actual_values = actual[key]
        if not isinstance(expected_values, list) or len(expected_values) != 3:
            errors.append(f"sidecar validation.region_stats.{name}.{key} is invalid")
            continue
        for index, (expected, observed) in enumerate(zip(expected_values, actual_values)):
            if not isinstance(expected, (int, float)) or not math.isclose(
                float(expected), float(observed), abs_tol=0.03
            ):
                errors.append(
                    f"{name} {key}[{index}] differs from sidecar: "
                    f"recorded={expected!r}, actual={observed}"
                )
    expected_entropy = recorded.get("entropy_l")
    if not isinstance(expected_entropy, (int, float)) or not math.isclose(
        float(expected_entropy), float(actual["entropy_l"]), abs_tol=0.03
    ):
        errors.append(
            f"{name} entropy differs from sidecar: "
            f"recorded={expected_entropy!r}, actual={actual['entropy_l']}"
        )


def load_sidecar(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON sidecar '{path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("sidecar root must be a JSON object")
    return payload


def validate(
    image_path: Path,
    sidecar_path: Path,
    expected_width: int | None,
    expected_height: int | None,
    allow_missing_sidecar: bool,
) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {
        "image": str(image_path),
        "sidecar": str(sidecar_path),
    }
    if not image_path.is_file():
        return [f"image does not exist or is not a file: {image_path}"], warnings, report

    try:
        with Image.open(image_path) as opened:
            opened.verify()
        with Image.open(image_path) as opened:
            opened.load()
            image = opened.convert("RGB")
            original_mode = opened.mode
    except (OSError, ValueError) as exc:
        return [f"image cannot be opened: {exc}"], warnings, report

    width, height = image.size
    report.update({"width": width, "height": height, "mode": original_mode})
    if width * 4 != height * 3:
        errors.append(f"image is not exactly 3:4: {width}x{height}")

    sidecar: dict[str, Any] | None = None
    if sidecar_path.is_file():
        try:
            sidecar = load_sidecar(sidecar_path)
        except ValueError as exc:
            errors.append(str(exc))
    elif allow_missing_sidecar:
        warnings.append(f"sidecar not found; metadata checks skipped: {sidecar_path}")
    else:
        errors.append(f"sidecar does not exist: {sidecar_path}")

    if sidecar is None:
        wanted_width = expected_width or DEFAULT_WIDTH
        wanted_height = expected_height or DEFAULT_HEIGHT
        if (width, height) != (wanted_width, wanted_height):
            errors.append(
                f"unexpected dimensions: got {width}x{height}, expected "
                f"{wanted_width}x{wanted_height}"
            )
        report["sha256"] = sha256_file(image_path)
        return errors, warnings, report

    if sidecar.get("schema") != "photo-to-illustration-card/v1":
        errors.append("unsupported or missing sidecar schema")
    output_meta = sidecar.get("output")
    if not isinstance(output_meta, dict):
        errors.append("sidecar output metadata is missing")
        output_meta = {}

    meta_width = output_meta.get("width")
    meta_height = output_meta.get("height")
    wanted_width = expected_width if expected_width is not None else meta_width
    wanted_height = expected_height if expected_height is not None else meta_height
    if not isinstance(wanted_width, int) or not isinstance(wanted_height, int):
        errors.append("expected dimensions are absent or invalid in the sidecar")
    elif (width, height) != (wanted_width, wanted_height):
        errors.append(
            f"unexpected dimensions: got {width}x{height}, expected "
            f"{wanted_width}x{wanted_height}"
        )
    if (meta_width, meta_height) != (width, height):
        errors.append(
            f"sidecar output dimensions {meta_width}x{meta_height} do not match image {width}x{height}"
        )

    image_hash = sha256_file(image_path)
    report["sha256"] = image_hash
    if output_meta.get("sha256") != image_hash:
        errors.append("image SHA-256 does not match sidecar; image or metadata was modified")

    partition = sidecar.get("partition")
    if not isinstance(partition, dict):
        errors.append("sidecar partition metadata is missing")
        partition = {}
    split_y = partition.get("split_y")
    photo_meta = partition.get("photo")
    lower_meta = partition.get("lower")
    if not isinstance(split_y, int) or not 0 < split_y < height:
        errors.append(f"invalid partition split_y: {split_y!r}")
        split_y = None
    if not isinstance(photo_meta, dict) or not is_box(photo_meta.get("box")):
        errors.append("invalid photo partition box")
        photo_box = None
    else:
        photo_box = photo_meta["box"]
    if not isinstance(lower_meta, dict) or not is_box(lower_meta.get("box")):
        errors.append("invalid lower partition box")
        lower_box = None
    else:
        lower_box = lower_meta["box"]

    canvas_box = [0, 0, width, height]
    if photo_box is not None and split_y is not None:
        expected_photo_box = [0, 0, width, split_y]
        if photo_box != expected_photo_box:
            errors.append(f"photo partition should be {expected_photo_box}, got {photo_box}")
    if lower_box is not None and split_y is not None:
        expected_lower_box = [0, split_y, width, height]
        if lower_box != expected_lower_box:
            errors.append(f"lower partition should be {expected_lower_box}, got {lower_box}")
    for label, box in (("photo", photo_box), ("lower", lower_box)):
        if box is not None and not box_inside(box, canvas_box):
            errors.append(f"{label} partition lies outside the image")

    parameters = sidecar.get("parameters")
    if not isinstance(parameters, dict):
        errors.append("sidecar parameters are missing")
        parameters = {}
    layout = parameters.get("layout")
    target_ratios = {"balanced": 0.50, "postcard": 0.70}
    if layout not in target_ratios:
        errors.append(f"unknown layout in sidecar: {layout!r}")
    elif split_y is not None and abs(split_y - round(height * target_ratios[layout])) > 1:
        errors.append(f"split_y does not match the {layout} layout")
    callout_positions: dict[str, float] = {}
    explicit_callout_positions: set[str] = set()
    for parameter_name, side in (("callout_left_y", "left"), ("callout_right_y", "right")):
        value = parameters.get(parameter_name)
        if value is None:
            # Early v1 sidecars predate configurable callout positions. Their
            # deterministic composer behavior was the current default: 0.5.
            callout_positions[side] = 0.5
        elif not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            errors.append(f"invalid {parameter_name} in sidecar: {value!r}")
        else:
            callout_positions[side] = float(value)
            explicit_callout_positions.add(side)

    illustration = sidecar.get("illustration")
    illustration_mode: str | None = None
    if not isinstance(illustration, dict) or not is_box(illustration.get("destination_box")):
        errors.append("illustration destination box is missing or invalid")
    elif lower_box is not None and not box_inside(illustration["destination_box"], lower_box):
        errors.append("illustration destination lies outside the lower panel")
    else:
        illustration_mode = illustration.get("mode_actual")
        if illustration_mode not in ("cutout", "panel"):
            errors.append(f"invalid illustration mode: {illustration_mode!r}")
        requested_mode = illustration.get("mode_requested")
        if requested_mode not in ("auto", "cutout", "panel"):
            errors.append(f"invalid requested illustration mode: {requested_mode!r}")
        if illustration.get("background_removal_attempted") is not False:
            errors.append("sidecar must confirm that no automatic background removal was attempted")
        alpha_extrema = illustration.get("alpha_extrema")
        if (
            not isinstance(alpha_extrema, list)
            or len(alpha_extrema) != 2
            or not all(isinstance(value, int) and 0 <= value <= 255 for value in alpha_extrema)
        ):
            errors.append("illustration alpha_extrema metadata is invalid")
        alpha_profile = illustration.get("alpha_profile")
        profile_valid = isinstance(alpha_profile, dict)
        if profile_valid:
            total = alpha_profile.get("total_pixels")
            transparent = alpha_profile.get("transparent_pixels_le_8")
            opaque = alpha_profile.get("opaque_pixels_ge_247")
            profile_valid = (
                isinstance(total, int)
                and total > 0
                and isinstance(transparent, int)
                and 0 <= transparent <= total
                and isinstance(opaque, int)
                and 0 <= opaque <= total
            )
        if not profile_valid:
            errors.append("illustration alpha_profile metadata is invalid")
            derived_effective_alpha = False
        else:
            derived_effective_alpha = (
                alpha_extrema == [0, 255]
                and transparent / total >= 0.001
                and opaque / total >= 0.001
            )
            if illustration.get("has_effective_alpha") is not derived_effective_alpha:
                errors.append("has_effective_alpha disagrees with alpha extrema/profile")
        if illustration_mode == "cutout" and not derived_effective_alpha:
            errors.append("cutout mode is recorded without transparent background and opaque subject")
        if illustration_mode == "panel" and lower_box is not None:
            if illustration.get("destination_box") != lower_box:
                errors.append("panel illustration must fill the complete lower partition")

    typography = sidecar.get("typography")
    if not isinstance(typography, dict):
        errors.append("sidecar typography metadata is missing")
    else:
        global_safe = typography.get("global_safe_box")
        if not is_box(global_safe):
            errors.append("typography.global_safe_box is invalid")
            global_safe = None
        elif lower_box is not None and not box_inside(global_safe, lower_box):
            errors.append("global typography safe box lies outside the lower panel")
        typography_positions = typography.get("callout_positions_requested")
        if not isinstance(typography_positions, dict) and explicit_callout_positions:
            errors.append("typography.callout_positions_requested is missing")
        elif isinstance(typography_positions, dict):
            for side in explicit_callout_positions:
                if typography_positions.get(side) != callout_positions[side]:
                    errors.append(f"typography callout {side} position disagrees with parameters")
        items = typography.get("items")
        if not isinstance(items, dict):
            errors.append("typography.items must be an object")
        else:
            expected_text = parameters.get("text") if isinstance(parameters.get("text"), dict) else {}
            resolved_fonts = (
                parameters.get("fonts_resolved")
                if isinstance(parameters.get("fonts_resolved"), dict)
                else {}
            )
            for name in ("title", "subtitle", "caption", "callout_left", "callout_right"):
                requested = expected_text.get(name, "")
                item = items.get(name)
                if requested and not isinstance(item, dict):
                    errors.append(f"text field {name} is requested but has no geometry record")
                    continue
                if not requested and item is not None:
                    warnings.append(f"text field {name} has geometry despite empty requested text")
                if not isinstance(item, dict):
                    continue
                bbox = item.get("bbox")
                safe_box = item.get("safe_box")
                if not is_box(bbox) or not is_box(safe_box):
                    errors.append(f"text field {name} has invalid bbox or safe_box")
                    continue
                if not box_inside(bbox, safe_box):
                    errors.append(f"text field {name} escapes its safe area: bbox={bbox}, safe={safe_box}")
                if global_safe is not None and not box_inside(safe_box, global_safe):
                    errors.append(f"text safe area for {name} escapes the global safe area")
                if not box_inside(safe_box, canvas_box):
                    errors.append(f"text safe area for {name} lies outside the image")
                if lower_box is not None and not box_inside(bbox, lower_box):
                    errors.append(f"text field {name} lies outside the lower panel")
                if item.get("text") != requested:
                    errors.append(f"text record for {name} does not match parameters.text")
                if name in ("callout_left", "callout_right"):
                    side = "left" if name == "callout_left" else "right"
                    requested_y = callout_positions.get(side)
                    recorded_requested_y = item.get("position_y_requested")
                    if side in explicit_callout_positions and recorded_requested_y != requested_y:
                        errors.append(f"text record for {name} has inconsistent requested y position")
                    if (
                        side not in explicit_callout_positions
                        and recorded_requested_y is not None
                        and recorded_requested_y != requested_y
                    ):
                        errors.append(f"text record for {name} has inconsistent legacy y position")
                    if requested_y is not None:
                        usable_height = safe_box[3] - safe_box[1]
                        expected_top = safe_box[1] + int(
                            max(0, usable_height - (bbox[3] - bbox[1])) * requested_y
                        )
                        if bbox[1] != expected_top:
                            errors.append(
                                f"text bbox for {name} does not match requested y position: "
                                f"expected top={expected_top}, got {bbox[1]}"
                            )
                if not isinstance(item.get("font_size"), int) or item.get("font_size", 0) <= 0:
                    errors.append(f"text field {name} has invalid font_size")
                font_path = item.get("font_path")
                if not isinstance(font_path, str) or not font_path:
                    errors.append(f"text field {name} has no resolved font path")
                elif resolved_fonts.get(name) != font_path:
                    errors.append(f"resolved font metadata for {name} is inconsistent")
                rendered_text = item.get("rendered_text")
                origin = item.get("origin")
                spacing = item.get("spacing")
                if (
                    isinstance(font_path, str)
                    and isinstance(rendered_text, str)
                    and isinstance(origin, list)
                    and len(origin) == 2
                    and all(isinstance(value, int) for value in origin)
                    and isinstance(spacing, int)
                    and spacing >= 0
                    and isinstance(item.get("font_size"), int)
                    and item["font_size"] > 0
                ):
                    try:
                        font = ImageFont.truetype(font_path, item["font_size"])
                        geometry_draw = ImageDraw.Draw(Image.new("L", (1, 1)))
                        measured = list(
                            geometry_draw.multiline_textbbox(
                                tuple(origin),
                                rendered_text,
                                font=font,
                                spacing=spacing,
                                align="left",
                            )
                        )
                    except OSError as exc:
                        errors.append(f"cannot load recorded font for {name}: {exc}")
                    else:
                        if measured != bbox:
                            errors.append(
                                f"text bbox for {name} does not match recorded font/origin: "
                                f"recorded={bbox}, measured={measured}"
                            )
                else:
                    errors.append(f"text geometry metadata for {name} is incomplete")

    validation = sidecar.get("validation")
    recorded_stats = validation.get("region_stats") if isinstance(validation, dict) else None
    if not isinstance(recorded_stats, dict):
        errors.append("sidecar validation.region_stats is missing")
    else:
        for name, box in (("photo", photo_box), ("lower", lower_box)):
            if box is None:
                continue
            recorded = recorded_stats.get(name)
            if not isinstance(recorded, dict):
                errors.append(f"recorded pixel stats for {name} are missing")
                continue
            actual = pixel_stats(image, box)
            compare_stats(name, recorded, actual, errors)
            if name == "lower" and illustration_mode == "cutout" and (
                max(actual["stddev_rgb"]) < 0.25 or actual["sample_unique_colors"] < 3
            ):
                errors.append("lower panel appears flat; paper texture/accent may be missing")
            if name == "lower" and illustration_mode == "panel" and (
                max(actual["stddev_rgb"]) < 0.10 or actual["sample_unique_colors"] < 2
            ):
                warnings.append("complete illustration panel is nearly uniform")
            if name == "photo" and max(actual["stddev_rgb"]) < 0.10:
                warnings.append("photo partition is nearly uniform (valid for a flat source, but unusual)")

    report["layout"] = layout
    report["split_y"] = split_y
    report["text_items"] = len(typography.get("items", {})) if isinstance(typography, dict) and isinstance(typography.get("items"), dict) else 0
    return errors, warnings, report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if PILLOW_IMPORT_ERROR is not None:
        print(
            "check_output.py: Pillow is required. Install it with "
            "'python3 -m pip install Pillow'.",
            file=sys.stderr,
        )
        return 2
    image_value = args.image or args.image_positional
    if not image_value:
        parser.error("provide an image as a positional argument or with --image")
    if args.image and args.image_positional:
        parser.error("use either positional image or --image, not both")
    image_path = Path(image_value).expanduser()
    sidecar_path = Path(args.sidecar).expanduser() if args.sidecar else Path(f"{image_path}.json")
    errors, warnings, report = validate(
        image_path,
        sidecar_path,
        args.expected_width,
        args.expected_height,
        args.allow_missing_sidecar,
    )
    report.update({"ok": not errors, "errors": errors, "warnings": warnings})
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
