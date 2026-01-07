# Mondrian Boogie Woogie Art Generator

A Python tool that recreates Piet Mondrian's "Broadway Boogie Woogie" style by analyzing a reference image, extracting its dominant color palette, and remapping every pixel to the nearest palette color. Always adds Mondrian-inspired colored tiles on light regions and along detected grid lanes to evoke the rhythmic, jazz-influenced composition of the original artwork.

## Features

- **Palette Extraction**: Automatically extracts dominant colors from reference images using median-cut quantization
- **Pixel-Perfect Remapping**: Maps every pixel to the nearest palette color without dithering for crisp, geometric results
- **Boogie Tiles**: Adds clustered micro-tiles on light background regions to create rhythmic patterns
- **Lane Detection**: Identifies grid-like structures and places tile runs along detected lanes
- **Diamond Masking**: Automatically detects and preserves the central diamond composition
- **Color Weighting**: Biases tile placement toward specific colors (yellow, red, blue, black)
- **Deterministic Mode**: Uses seed for reproducible tile size, density, and placement patterns
- **Light Region Masking**: Export a binary mask of light/white/gray regions for inspection or reuse

## Prerequisites

- Python 3.13+ (virtualenv already present at `venv/`)
- Pillow (bundled inside `venv`)

## Installation

If not using the bundled virtualenv:

```bash
pip install Pillow
```

## Basic Usage

### Windows Command

```cmd
python mondrian_boogie_woogie.py --input art.JPG --output recreated.png --tile-seed 12345
```

**Note**: 
- Boogie tiles and lane tiles are **always enabled**
- Tile size (140-150px) and tile density (0.38-0.50) are **randomly generated** by the RNG based on the seed
- The `--tile-seed` parameter is **required** for reproducible tile size, density, and placement patterns

### Palette Extraction

| Option | Description | Default |
|--------|-------------|---------|
| `--max-colors N` | Number of dominant colors to extract | 12 |
| `--max-dim PX` | Resize longest side to this dimension | None |

### Boogie Tiles (Always Enabled)

| Option | Description | Default |
|--------|-------------|---------|
| `--tile-size PX` | ~~Side length of tiles in pixels~~ | **Randomly generated (5-15px)** |
| `--tile-density FLOAT` | ~~Probability of placing tiles (0-1)~~ | **Randomly generated (0.05-0.15)** |
| `--tile-brightness-threshold N` | Min brightness for light background | 220 |
| `--tile-gray-range N` | Max channel spread for gray detection | 25 |
| `--tile-seed N` | Seed for deterministic tile size, density, and placement (required) | Required |
| `--boogie-mask PATH` | Save mask of painted boogie tiles | None |

**Note**: Boogie tiles are always enabled. Tile size and density are automatically randomized by the RNG based on the seed. The chosen values are printed to the console.

### Lane Tiles (Always Enabled)

| Option | Description | Default |
|--------|-------------|---------|
| `--lane-density FLOAT` | Probability of starting lane run (0-1) | 0.20 |
| `--lane-run-length N` | Nominal tiles per run (±1 variance) | 5 |
| `--lane-mask PATH` | Save lane detection + placement mask | None |

**Note**: Lane tiles are always enabled by default.

### Color Weighting

Control the probability distribution for tile colors:

| Option | Description | Default |
|--------|-------------|---------|
| `--yellow-weight FLOAT` | Bias weight for yellow tiles | 3.0 |
| `--red-weight FLOAT` | Bias weight for red tiles | 2.0 |
| `--blue-weight FLOAT` | Bias weight for blue tiles | 1.0 |
| `--black-weight FLOAT` | Bias weight for black/dark tiles | 0.5 |

### Diamond Masking

| Option | Description | Default |
|--------|-------------|---------|
| `--disable-diamond-mask` | Allow tiles outside detected diamond | False |
| `--diamond-white-threshold N` | Brightness for diamond boundary detection | 245 |

### Light Region Mask

Export a binary mask of light / white / gray regions using the same thresholds that gate boogie/lane tiles:

| Option | Description | Default |
|--------|-------------|---------|
| `--light-mask PATH` | Save mask of pixels that are both bright and near-gray | None |
| `--tile-brightness-threshold N` | Min brightness for light detection | 220 |
| `--tile-gray-range N` | Max channel spread for gray detection | 25 |

## How It Works

### 1. Palette Extraction
The script uses Pillow's median-cut quantization to identify the most dominant colors in your reference image, typically extracting 12 colors that represent the primary hues, blacks, and off-whites.

### 2. Pixel Remapping
Every pixel is mapped to its nearest palette color using Euclidean distance in RGB space, with no dithering. This creates clean, flat color blocks reminiscent of Mondrian's style.

### 3. Diamond Detection (Optional)
Automatically identifies the central painted region by detecting non-white content and fitting a rotated square (diamond) around it. Tiles are confined to this region unless disabled.

### 4. Tile Placement

**Boogie Tiles**: Scans the image on a grid and places short clusters (1-3 tiles) of colored squares on light background regions. Clusters can be horizontal or vertical, creating rhythmic patterns.

**Lane Tiles**: Detects color discontinuities (edges) to approximate grid lines, then places runs of tiles along these detected lanes in straight sequences.

### 5. Color Biasing
Tiles are not selected uniformly from the palette. Colors closer to yellow, red, blue, or black targets receive higher probability weights, which you can customize via command-line flags.

## Tile Generation

The script uses a seeded random number generator to ensure reproducible results:
- Tile size is randomly chosen (140-150 pixels) based on the seed
- Tile density is randomly chosen (0.38-0.50) based on the seed
- Tile placement patterns are determined by the seed
- Same seed always produces identical tile size, density, and placement patterns
- Perfect for reproducibility in production or testing
- Allows you to "lock in" a pattern you like
- Anyone with the same seed and parameters gets the same output

**Example:**
```cmd
python mondrian_boogie_woogie.py --input art.JPG --output recreated.png --tile-seed 12345
```

## Output Files

- **Main Output**: The recreated artwork with optional tile augmentation
- **Palette Strip**: Horizontal visualization showing extracted colors in order
- **Boogie Mask**: Binary mask showing where boogie tiles were placed (white = painted)
- **Lane Mask**: Visualization of detected lanes and placed lane tiles
- **Light Region Mask**: Binary mask of bright, near-gray areas in the source

## Tips

- Start with default parameters and adjust incrementally
- The `--tile-seed` parameter is required - use different seeds to explore different tile size/density combinations
- Tile size (140-150px) and density (0.38-0.50) are randomly generated based on the seed - check console output to see chosen values
- Boogie tiles and lane tiles are always enabled for rich, layered effects
- Save masks to understand where tiles are being placed
- Adjust color weights to match your artistic vision

## Technical Notes

- The script handles very large images via `Image.MAX_IMAGE_PIXELS = None`
- Use `--max-dim` to resize before processing for faster execution
- Tile placement respects the diamond boundary by default for authentic composition
- Random number generation uses a single seeded RNG for full determinism (seed is required)
- Color distance calculations use Euclidean RGB space
