# Displate One-Click Preparer

A fast, local Windows web tool for technical Displate artwork preparation, verification, and print-ready export.

## Features

- **Strict Specification Preflight**: Checks dimensions, ratio, color space, and metadata against official Displate artist requirements.
- **Aspect Ratio & Smart Cropping**: Automatically checks 5:7 (or 7:5) Displate ratio with customizable protected anchors (center, top, bottom).
- **Auto AI Upscaling**: Integrated Real-ESRGAN NCNN Vulkan (GPU-accelerated) to upscale images under 2900 px shortest side up to print requirements.
- **Smart Flat Area & Micro-Texture Engine**:
  - Automatically identifies large low-detail flat areas (such as solid skies or backgrounds).
  - Generates deterministic, edge-aware micro-textures that enhance print aesthetics while keeping strong edges and subjects untouched.
- **Color Profile & DPI Compliance**: Converts and embeds sRGB IEC61966-2.1 with 300 DPI metadata.
- **Safe Area Visualizer**: Produces a preview with a 200 px safe-zone boundary so important artwork elements aren't cut or folded.
- **Print-Ready Export**: Produces high-quality JPEGs optimized under the 30 MB limit along with a structured JSON inspection report and ZIP archive.
- **Near-Duplicate Detection**: Perceptual hash (pHash) tracking to detect identical or near-duplicate uploads.

---

## Quick Start (Windows)

1. Clone or extract this repository.
2. Double-click `START_PROGRAMME.bat` (or `START_APP.bat`).
3. On first launch, the script automatically sets up the Python virtual environment and installs dependencies.
4. Your browser will automatically open at:
   ```text
   http://127.0.0.1:7972
   ```

---

## Requirements

- **OS**: Windows 10/11 (64-bit)
- **Python**: 3.10+ (Python 3.11 recommended; installer script attempts auto-install via `winget` if missing)
- **GPU (Optional)**: Vulkan-compatible GPU for accelerated Real-ESRGAN upscaling.

---

## Running Tests

To run the full unit test suite:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
```

---

## Displate Artwork Guidelines Reference

- **Dimensions**: Minimum 2900 px on the shortest side (recommended: 4000 × 5600 px for portrait, 5600 × 4000 px for landscape).
- **Ratio**: Exactly 1:1.4 (5:7 ratio).
- **Resolution**: 300 DPI minimum.
- **Color Space**: sRGB 8-bit.
- **File Format**: JPEG under 30 MB.
- **Safe Margins**: Minimum 200 px margin free of essential text, logos, or critical signatures.
