"""
Generate a recreation of Piet Mondrian's "Broadway Boogie Woogie" by quantizing the
reference image to its dominant palette and remapping every pixel to the nearest palette
color (no dithering). Optionally add Mondrian-like "boogie" tiles on light regions and
along detected grid lanes to better evoke the original composition.

Basic usage:
    venv/bin/python mondrian_boogie_woogie.py --input art.JPG --output recreated.png

Key optional flags (see --help for all):
    --max-colors N                 Number of dominant colors to extract (default: 12).
    --max-dim PX                   Resize the longest side before processing.
    --palette-strip PATH           Save a palette visualization strip.
    --disable-diamond-mask         Allow painting outside the detected diamond (default: masked).
    --tile-seed N                  Seed for RNG (required - tile size and density are randomly generated).
    --lane-density FLOAT           Chance to start a lane run (default: 0.20).
    
Note: Boogie tiles and lane tiles are always enabled by default.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from PIL import Image, ImageChops, ImageFilter, ImageStat

# Allow very large images without triggering the Pillow bomb guard.
Image.MAX_IMAGE_PIXELS = None

Color = Tuple[int, int, int]


@dataclass
class ColorWeights:
    yellow: float = 3.0
    red: float = 2.0
    blue: float = 1.0
    black: float = 0.5


def color_distance(c1: Color, c2: Color) -> float:
    """Euclidean distance between two RGB colors."""
    return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


def mean_brightness(color: Color) -> float:
    """Average channel value as a brightness proxy."""
    return (color[0] + color[1] + color[2]) / 3.0


def is_grayish(color: Color, gray_range: int) -> bool:
    """Return True when a color sits within a narrow gray band (max-min <= gray_range)."""
    return max(color) - min(color) <= gray_range


def is_light_background(color: Color, brightness_threshold: int, gray_range: int) -> bool:
    """
    Decide whether a tile represents a light neutral background block.
    Requires high brightness and low channel spread (near gray/off-white).
    """
    return mean_brightness(color) >= brightness_threshold and is_grayish(
        color, gray_range
    )


def mean_tile_color(img: Image.Image, x: int, y: int, size: int) -> Color:
    """Compute the average RGB color for a square tile."""
    box = (x, y, x + size, y + size)
    stat = ImageStat.Stat(img.crop(box))
    return (int(stat.mean[0]), int(stat.mean[1]), int(stat.mean[2]))


def generate_light_mask(
    img: Image.Image, brightness_threshold: int, gray_range: int
) -> Image.Image:
    """
    Build a binary mask (L mode) where 255 marks pixels that are both bright enough and
    sufficiently gray/neutral based on the provided thresholds.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    src = rgb.load()
    mask = Image.new("L", (w, h), 0)
    dest = mask.load()
    for y in range(h):
        for x in range(w):
            r, g, b = src[x, y]
            if mean_brightness((r, g, b)) >= brightness_threshold and is_grayish(
                (r, g, b), gray_range
            ):
                dest[x, y] = 255
    return mask


def grid_positions(length: int, tile_size: int) -> List[int]:
    """Generate aligned tile start coordinates that fit entirely within the image."""
    return list(range(0, length - tile_size + 1, tile_size))


def filter_palette_for_tiles(
    palette: Sequence[Color], brightness_cutoff: int = 238, gray_range: int = 20
) -> List[Color]:
    """
    Remove near-white / gray palette entries so augmented tiles stay colorful.
    Falls back to the full palette if everything gets filtered out.
    """
    filtered = [
        c
        for c in palette
        if not (mean_brightness(c) >= brightness_cutoff and is_grayish(c, gray_range))
    ]
    return filtered or list(palette)


def biased_palette_weights(palette: Sequence[Color], weights: ColorWeights) -> List[float]:
    """
    Produce probability weights for each palette color using proximity to color families
    and user-provided bias multipliers.
    """
    families = [
        ((248, 220, 60), weights.yellow),  # warm yellow target
        ((215, 45, 45), weights.red),  # deep red target
        ((60, 95, 200), weights.blue),  # muted blue target
        ((25, 25, 25), weights.black),  # black/very dark
    ]
    results: List[float] = []
    for color in palette:
        score = 0.0
        for center, family_weight in families:
            dist = color_distance(color, center)
            affinity = max(0.05, (255.0 - dist) / 255.0)  # Closer colors get more weight.
            score += affinity * family_weight
        results.append(max(1e-3, score))
    return results


def choose_biased_color(
    palette: Sequence[Color], weights: Sequence[float], rng: random.Random
) -> Color:
    """Select a palette entry according to provided probability weights."""
    return rng.choices(population=palette, weights=weights, k=1)[0]


def paste_tile(
    img: Image.Image, x: int, y: int, size: int, color: Color, mask: Image.Image | None
) -> None:
    """Paint a solid square tile and optionally mark it in a mask."""
    block = Image.new("RGB", (size, size), color)
    img.paste(block, (x, y))
    if mask is not None:
        mask.paste(255, (x, y, x + size, y + size))


def detect_diamond_mask(
    img: Image.Image, white_threshold: int = 245, margin: int = 0
) -> Image.Image:
    """
    Detect the central diamond by thresholding non-white content and deriving a rotated
    square (|dx| + |dy| <= r) that encloses it. Returns an L-mode mask.
    """
    gray = img.convert("L")
    # Identify pixels that are not near-white to approximate the painted area.
    content_mask = gray.point(lambda v: 255 if v < white_threshold else 0, mode="L")
    bbox = content_mask.getbbox()
    w, h = img.size
    if bbox:
        x0, y0, x1, y1 = bbox
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        half = min(x1 - x0, y1 - y0) / 2.0
    else:
        # Fallback to a centered diamond occupying most of the frame.
        cx = w / 2.0
        cy = h / 2.0
        half = min(w, h) / 2.2
    radius = max(4.0, half - margin)

    mask = Image.new("L", (w, h), 0)
    pixels = mask.load()
    for y in range(h):
        dy = abs(y - cy)
        # Precompute max dx allowed for this row: |dx| + dy <= radius -> |dx| <= radius - dy
        max_dx = radius - dy
        if max_dx < 0:
            continue
        x_left = int(max(cx - max_dx, 0))
        x_right = int(min(cx + max_dx, w - 1))
        for x in range(x_left, x_right + 1):
            pixels[x, y] = 255
    return mask


def extract_palette(img: Image.Image, max_colors: int) -> List[Tuple[Color, int]]:
    """
    Extract the top `max_colors` colors from the image using median-cut quantization.
    Returns a list of (color, pixel_count) sorted by frequency descending.
    """
    paletted = img.convert("RGB").quantize(colors=max_colors, method=Image.MEDIANCUT)  # Reduce image to a limited palette via median cut.
    palette_bytes = paletted.getpalette()[: max_colors * 3]  # Raw palette list (R, G, B per entry).

    def color_for_index(idx: int) -> Color:
        base = idx * 3  # Each palette color occupies three consecutive slots.
        return (
            palette_bytes[base],
            palette_bytes[base + 1],
            palette_bytes[base + 2],
        )

    counts = paletted.getcolors()  # Frequency per palette index.
    if not counts:  # Safety: empty images.
        return []

    # Sort by how often each palette index appears so we can report coverage.
    ranked = sorted(((count, idx) for count, idx in counts), reverse=True)
    return [(color_for_index(idx), count) for count, idx in ranked]


def build_palette_image(colors: Sequence[Color]) -> Image.Image:
    """Create a Pillow palette image from a sequence of RGB tuples."""
    palette_img = Image.new("P", (1, 1))  # 1x1 placeholder to host the palette data.
    flat: List[int] = []  # Flattened palette buffer: [R, G, B, R, G, B, ...].
    for color in colors:  # Fill with provided colors in order.
        flat.extend(color)
    # Pad the palette to 256 * 3 entries as required by Pillow.
    flat.extend([0] * (256 * 3 - len(flat)))
    palette_img.putpalette(flat)  # Assign palette bytes to the image.
    return palette_img


def remap_to_palette(img: Image.Image, palette: Sequence[Color]) -> Image.Image:
    """
    Map every pixel to the nearest color from the provided palette (no dithering),
    then convert back to RGB for a crisp recreation.
    """
    palette_img = build_palette_image(palette)  # Build a palette image usable by quantize.
    return img.convert("RGB").quantize(palette=palette_img, dither=Image.NONE).convert(
        "RGB"
    )


def resize_if_needed(img: Image.Image, max_dim: int | None) -> Image.Image:
    """Optionally resize the image so its longest side equals `max_dim`."""
    if not max_dim:  # Skip resizing when the flag is absent.
        return img
    w, h = img.size  # Original width and height.
    scale = max(w, h)  # Longest side.
    if scale <= max_dim:  # Already within limit.
        return img
    ratio = max_dim / float(scale)  # Uniform scale factor.
    new_size = (int(w * ratio), int(h * ratio))  # Maintain aspect ratio.
    return img.resize(new_size, Image.Resampling.LANCZOS)  # High-quality downscale.


def save_palette_strip(colors: Iterable[Color], dest: Path, height: int = 80) -> None:
    """Save a horizontal strip visualizing the palette order."""
    colors = list(colors)  # Materialize iterable so we can iterate multiple times.
    if not colors:  # Nothing to render.
        return
    width_per_color = 120  # Block width per palette entry.
    strip = Image.new("RGB", (width_per_color * len(colors), height), (255, 255, 255))  # White background strip.
    for i, color in enumerate(colors):  # Paint each palette color as a block.
        block = Image.new("RGB", (width_per_color, height), color)
        strip.paste(block, (i * width_per_color, 0))
    dest.parent.mkdir(parents=True, exist_ok=True)  # Ensure output folder exists.
    strip.save(dest)  # Write the palette visualization.


def compute_lane_mask(img: Image.Image, edge_threshold: float = 45.0) -> Image.Image:
    """
    Approximate Mondrian-like grid lanes by detecting color discontinuities.
    Returns an L-mode mask (255 where edges/lanes are likely).
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    src = rgb.load()
    mask = Image.new("L", (w, h), 0)
    dest = mask.load()
    for y in range(h):
        for x in range(w):
            if x + 1 < w and color_distance(src[x, y], src[x + 1, y]) > edge_threshold:
                dest[x, y] = 255
                dest[x + 1, y] = 255
            if y + 1 < h and color_distance(src[x, y], src[x, y + 1]) > edge_threshold:
                dest[x, y] = 255
                dest[x, y + 1] = 255
    # Expand edges slightly to create thin stripes that can receive tiles.
    return mask.filter(ImageFilter.MaxFilter(size=3))


def tile_mask_coverage(mask: Image.Image, x: int, y: int, size: int) -> float:
    """Fraction of pixels within a tile that are marked in a mask (0..1)."""
    stat = ImageStat.Stat(mask.crop((x, y, x + size, y + size)))
    return stat.mean[0] / 255.0


def add_boogie_tiles(
    img: Image.Image,
    palette: Sequence[Color],
    tile_size: int,
    tile_density: float,
    brightness_threshold: int,
    gray_range: int,
    rng: random.Random,
    color_weights: ColorWeights,
    allowed_mask: Image.Image | None = None,
) -> Tuple[Image.Image, Image.Image]:
    """
    Paint small, intentionally clustered tiles on light background regions.
    Returns the augmented image and an L-mode mask of where tiles were painted.
    """
    canvas = img.copy()
    paint_mask = Image.new("L", img.size, 0)
    eligible_palette = filter_palette_for_tiles(palette)
    palette_weights = biased_palette_weights(eligible_palette, color_weights)
    xs = grid_positions(img.size[0], tile_size)
    ys = grid_positions(img.size[1], tile_size)
    for y in ys:
        for x in xs:
            mean_color = mean_tile_color(img, x, y, tile_size)
            if not is_light_background(mean_color, brightness_threshold, gray_range):
                continue
            if rng.random() > tile_density:
                continue
            run_len = rng.randint(1, 3)  # Short clusters keep things lively.
            orientation = rng.choice(("h", "v"))
            for step in range(run_len):
                tx = x + (step * tile_size if orientation == "h" else 0)
                ty = y + (step * tile_size if orientation == "v" else 0)
                if tx + tile_size > img.size[0] or ty + tile_size > img.size[1]:
                    break
                if not is_light_background(
                    mean_tile_color(img, tx, ty, tile_size),
                    brightness_threshold,
                    gray_range,
                ):
                    continue
                if allowed_mask and tile_mask_coverage(allowed_mask, tx, ty, tile_size) < 0.98:
                    continue
                color = choose_biased_color(eligible_palette, palette_weights, rng)
                paste_tile(canvas, tx, ty, tile_size, color, paint_mask)
    return canvas, paint_mask


def add_lane_tiles(
    img: Image.Image,
    palette: Sequence[Color],
    lane_mask: Image.Image,
    tile_size: int,
    lane_density: float,
    lane_run_length: int,
    rng: random.Random,
    color_weights: ColorWeights,
    brightness_threshold: int,
    gray_range: int,
    allowed_mask: Image.Image | None = None,
) -> Tuple[Image.Image, Image.Image]:
    """
    Paint short runs of tiles along detected horizontal/vertical lane stripes.
    Returns the augmented image and an L-mode mask combining lane detection and placements.
    """
    canvas = img.copy()
    placement_mask = lane_mask.copy()
    eligible_palette = filter_palette_for_tiles(palette)
    palette_weights = biased_palette_weights(eligible_palette, color_weights)
    xs = grid_positions(img.size[0], tile_size)
    ys = grid_positions(img.size[1], tile_size)
    if not xs or not ys:
        return canvas, placement_mask

    # Identify which grid tiles sit on top of a detected lane stripe.
    on_lane = [[False for _ in xs] for _ in ys]
    for yi, y in enumerate(ys):
        for xi, x in enumerate(xs):
            if tile_mask_coverage(lane_mask, x, y, tile_size) >= 0.15:
                if allowed_mask and tile_mask_coverage(allowed_mask, x, y, tile_size) < 0.98:
                    continue
                if not is_light_background(
                    mean_tile_color(img, x, y, tile_size), brightness_threshold, gray_range
                ):
                    continue
                on_lane[yi][xi] = True

    # Horizontal sequences.
    for yi, y in enumerate(ys):
        xi = 0
        while xi < len(xs):
            if not on_lane[yi][xi] or rng.random() > lane_density:
                xi += 1
                continue
            run = max(1, lane_run_length + rng.randint(-1, 1))
            for step in range(run):
                gx = xi + step
                if gx >= len(xs) or not on_lane[yi][gx]:
                    break
                tx = xs[gx]
                ty = y
                if allowed_mask and tile_mask_coverage(allowed_mask, tx, ty, tile_size) < 0.98:
                    continue
                color = choose_biased_color(eligible_palette, palette_weights, rng)
                paste_tile(canvas, tx, ty, tile_size, color, placement_mask)
            xi += run

    # Vertical sequences.
    for xi, x in enumerate(xs):
        yi = 0
        while yi < len(ys):
            if not on_lane[yi][xi] or rng.random() > lane_density:
                yi += 1
                continue
            run = max(1, lane_run_length + rng.randint(-1, 1))
            for step in range(run):
                gy = yi + step
                if gy >= len(ys) or not on_lane[gy][xi]:
                    break
                tx = x
                ty = ys[gy]
                if allowed_mask and tile_mask_coverage(allowed_mask, tx, ty, tile_size) < 0.98:
                    continue
                color = choose_biased_color(eligible_palette, palette_weights, rng)
                paste_tile(canvas, tx, ty, tile_size, color, placement_mask)
            yi += run

    return canvas, placement_mask


def report_palette(palette: Sequence[Tuple[Color, int]], total_pixels: int) -> str:
    lines = ["Palette coverage:"]  # Header line.
    for color, count in palette:  # Walk palette entries and their frequencies.
        pct = 100 * count / total_pixels  # Percent of total pixels.
        lines.append(
            f"  rgb{color}: {count:,} px ({pct:0.2f}%)"
        )
    return "\n".join(lines)  # Join all lines into a single report string.


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(  # CLI parser with description.
        description="Recreate Broadway Boogie Woogie by per-pixel palette remapping."
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to the reference image (e.g., art.JPG).",
    )
    parser.add_argument(
        "--output",
        default=Path("recreated.png"),
        type=Path,
        help="Where to write the recreated image.",
    )
    parser.add_argument(
        "--max-colors",
        default=12,
        type=int,
        help="Number of dominant colors to extract from the source (default: 12).",
    )
    parser.add_argument(
        "--max-dim",
        type=int,
        default=None,
        help="If set, resize the longest image side to this many pixels before processing.",
    )
    parser.add_argument(
        "--palette-strip",
        type=Path,
        default=None,
        help="Optional path to save a small strip that visualizes the extracted palette.",
    )
    parser.add_argument(
        "--tile-brightness-threshold",
        type=int,
        default=220,
        help="Minimum average brightness for a tile to count as light background.",
    )
    parser.add_argument(
        "--tile-gray-range",
        type=int,
        default=25,
        help="Maximum channel spread for a tile to be considered gray/off-white.",
    )
    parser.add_argument(
        "--tile-seed",
        type=int,
        required=True,
        help="Seed for deterministic tile placement (applies to both boogie and lane modes).",
    )
    parser.add_argument(
        "--lane-density",
        type=float,
        default=0.20,
        help="Probability of starting a run on a lane tile (0-1).",
    )
    parser.add_argument(
        "--lane-run-length",
        type=int,
        default=5,
        help="Nominal number of tiles in a lane sequence (varies by ±1).",
    )
    parser.add_argument(
        "--yellow-weight",
        type=float,
        default=3.0,
        help="Bias weight for yellow palette entries (higher == more likely).",
    )
    parser.add_argument(
        "--red-weight",
        type=float,
        default=2.0,
        help="Bias weight for red palette entries.",
    )
    parser.add_argument(
        "--blue-weight",
        type=float,
        default=1.0,
        help="Bias weight for blue palette entries.",
    )
    parser.add_argument(
        "--black-weight",
        type=float,
        default=0.5,
        help="Bias weight for black/dark palette entries.",
    )
    parser.add_argument(
        "--boogie-mask",
        type=Path,
        default=None,
        help="If set, save a mask image where boogie tiles were painted.",
    )
    parser.add_argument(
        "--lane-mask",
        type=Path,
        default=None,
        help="If set, save a visualization of detected lanes plus painted lane tiles.",
    )
    parser.add_argument(
        "--light-mask",
        type=Path,
        default=None,
        help=(
            "If set, save a binary mask of light/white/gray regions using the brightness "
            "and gray-range thresholds."
        ),
    )
    parser.add_argument(
        "--disable-diamond-mask",
        action="store_true",
        help="Skip automatic diamond detection; allows tiles anywhere in the frame.",
    )
    parser.add_argument(
        "--diamond-white-threshold",
        type=int,
        default=245,
        help="Brightness threshold used to detect the diamond boundary (lower is stricter).",
    )
    return parser.parse_args()  # Parse CLI flags and return Namespace.


def main() -> None:
    args = parse_args()  # Collect CLI options.
    src_path: Path = args.input  # Source image path.
    out_path: Path = args.output  # Destination image path.

    if not src_path.exists():  # Validate input early.
        raise SystemExit(f"Input image not found: {src_path}")

    img = Image.open(src_path)  # Load source image.
    img = resize_if_needed(img, args.max_dim)  # Optional downscale.

    # Build a reduced palette from the source image, then remap every pixel to it.
    palette_with_counts = extract_palette(img, args.max_colors)
    if not palette_with_counts:  # Abort if quantization failed.
        raise SystemExit("Failed to extract palette from the input image.")

    palette = [color for color, _ in palette_with_counts]  # Strip counts, keep colors.
    recreated = remap_to_palette(img, palette)  # Apply palette mapping.
    lane_detection_base = recreated.copy()  # Keep an unmodified copy for lane detection.

    diamond_mask = None
    if not args.disable_diamond_mask:
        diamond_mask = detect_diamond_mask(
            recreated, white_threshold=args.diamond_white_threshold, margin=0
        )
        # Keep the exterior pure white and confine later placements to the diamond.
        clean_bg = Image.new("RGB", recreated.size, (255, 255, 255))
        clean_bg.paste(recreated, mask=diamond_mask)
        recreated = clean_bg
        lane_detection_base = recreated.copy()

    rng = random.Random(args.tile_seed)  # Single RNG to keep deterministic when seeded.
    weights = ColorWeights(
        yellow=args.yellow_weight,
        red=args.red_weight,
        blue=args.blue_weight,
        black=args.black_weight,
    )
    # Randomly choose tile size (140-150 pixels) and density (0.38-0.50)
    tile_size = rng.randint(140, 150)
    tile_density = rng.uniform(0.38, 0.5)
    print(f"Random tile size: {tile_size}px, tile density: {tile_density:.3f}")
    lane_density = min(1.0, max(0.0, args.lane_density))
    lane_run_length = max(1, args.lane_run_length)

    # Always add boogie tiles (clustered micro-tiles on light regions)
    recreated, boogie_mask = add_boogie_tiles(
        recreated,
        palette,
        tile_size=tile_size,
        tile_density=tile_density,
        brightness_threshold=args.tile_brightness_threshold,
        gray_range=args.tile_gray_range,
        rng=rng,
        color_weights=weights,
        allowed_mask=diamond_mask,
    )
    if args.boogie_mask:
        args.boogie_mask.parent.mkdir(parents=True, exist_ok=True)
        boogie_mask.save(args.boogie_mask)
        print(f"Saved boogie mask to: {args.boogie_mask}")

    # Always add lane tiles (runs along detected grid lanes)
    lane_detection = compute_lane_mask(lane_detection_base)
    if diamond_mask:
        lane_detection = ImageChops.multiply(lane_detection, diamond_mask)
    recreated, lane_mask = add_lane_tiles(
        recreated,
        palette,
        lane_mask=lane_detection,
        tile_size=tile_size,
        lane_density=lane_density,
        lane_run_length=lane_run_length,
        rng=rng,
        color_weights=weights,
        brightness_threshold=args.tile_brightness_threshold,
        gray_range=args.tile_gray_range,
        allowed_mask=diamond_mask,
    )
    if args.lane_mask:
        args.lane_mask.parent.mkdir(parents=True, exist_ok=True)
        lane_mask.save(args.lane_mask)
        print(f"Saved lane mask to: {args.lane_mask}")

    if args.light_mask:
        light_mask = generate_light_mask(
            img,
            brightness_threshold=args.tile_brightness_threshold,
            gray_range=args.tile_gray_range,
        )
        args.light_mask.parent.mkdir(parents=True, exist_ok=True)
        light_mask.save(args.light_mask)
        print(
            f"Saved light-region mask to: {args.light_mask} "
            f"(threshold={args.tile_brightness_threshold}, gray_range={args.tile_gray_range})"
        )

    # Re-apply the diamond mask at the end to guarantee a clean white exterior.
    if diamond_mask:
        clean_bg = Image.new("RGB", recreated.size, (255, 255, 255))
        clean_bg.paste(recreated, mask=diamond_mask)
        recreated = clean_bg

    out_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure output folder exists.
    recreated.save(out_path)  # Write recreated image.

    total_pixels = recreated.size[0] * recreated.size[1]  # Image area for percentages.
    print(report_palette(palette_with_counts, total_pixels))  # Console coverage report.
    print(f"Saved recreation to: {out_path} (size={recreated.size[0]}x{recreated.size[1]})")

    if args.palette_strip:  # Optionally emit palette strip visualization.
        save_palette_strip(palette, args.palette_strip)
        print(f"Saved palette strip to: {args.palette_strip}")


if __name__ == "__main__":
    main()  # Entrypoint: run when executed as a script.
