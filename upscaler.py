from __future__ import annotations

import logging
import math
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
REALESRGAN_DIR = BASE_DIR / "bin" / "realesrgan"
REALESRGAN_EXE = REALESRGAN_DIR / "realesrgan-ncnn-vulkan.exe"
MODELS_DIR = REALESRGAN_DIR / "models"

AVAILABLE_MODELS = {
    "realesrgan-x4plus": "General Art & Photorealistic (x4)",
    "realesrgan-x4plus-anime": "Anime, Manga & Clean 2D Illustration (x4)",
    "realesr-animevideov3-x2": "Fast Animation (x2)",
    "realesr-animevideov3-x3": "Fast Animation (x3)",
    "realesr-animevideov3-x4": "Fast Animation (x4)",
}

NATIVE_4X_MODELS = {"realesrgan-x4plus", "realesrgan-x4plus-anime"}


def is_upscaler_available() -> bool:
    """Returns True if the Real-ESRGAN binary and model directory exist."""
    return REALESRGAN_EXE.is_file() and MODELS_DIR.is_dir()


def get_upscaler_info() -> dict[str, Any]:
    """Returns diagnostic information about the local upscaler."""
    available = is_upscaler_available()
    return {
        "available": available,
        "engine": "Real-ESRGAN NCNN Vulkan",
        "executable": str(REALESRGAN_EXE) if available else None,
        "models_dir": str(MODELS_DIR) if available else None,
        "available_models": AVAILABLE_MODELS,
        "default_model": "realesrgan-x4plus",
    }


def calculate_required_scale(width: int, height: int, target_short_side: int = 2900) -> int:
    """
    Computes required integer scale factor (2, 3, or 4) to ensure shortest side >= target.
    If shortest side is already >= target, returns 1 (no upscale needed).
    """
    short_side = min(width, height)
    if short_side >= target_short_side:
        return 1
    
    needed = target_short_side / short_side
    if needed <= 2.0:
        return 2
    elif needed <= 3.0:
        return 3
    else:
        return 4


def upscale_image(
    input_path: Path,
    output_path: Path,
    scale: int = 4,
    model_name: str = "realesrgan-x4plus",
    gpu_id: str = "auto",
    tile_size: int = 0,
) -> dict[str, Any]:
    """
    Executes Real-ESRGAN NCNN Vulkan upscaling on input_path and saves to output_path.
    Returns metadata about the upscaling operation.

    Quality Guarantee:
    - 4x neural network models (realesrgan-x4plus and realesrgan-x4plus-anime) are strictly
      executed at their native 4x scale. Passing -s 2 or -s 3 to the binary causes severe
      tile-boundary tearing and blocky/pixelated grid defects. If scale < 4 is requested,
      the native 4x output is downsampled to the requested size using high-fidelity Lanczos
      resampling, ensuring completely smooth curves and pristine details.
    - realesr-animevideov3 uses its respective native x2, x3, or x4 weights.
    """
    if not is_upscaler_available():
        raise RuntimeError(
            f"Real-ESRGAN binary not found at {REALESRGAN_EXE}. Please verify installation."
        )

    if model_name not in AVAILABLE_MODELS:
        model_name = "realesrgan-x4plus"

    scale = max(2, min(4, scale))

    # Read original dimensions after EXIF orientation normalization
    with Image.open(input_path) as orig:
        transposed = ImageOps.exif_transpose(orig)
        orig_w, orig_h = transposed.size
        needs_orientation_fix = (transposed.size != orig.size) or bool(
            orig.getexif() and 0x0112 in orig.getexif()
        )

    # Determine native CLI model name and native execution scale
    downsample_needed = False
    temp_transposed_path: Path | None = None
    exec_input_path = input_path

    if needs_orientation_fix:
        temp_transposed_path = output_path.parent / f"_temp_exif_{input_path.stem}.png"
        transposed.save(temp_transposed_path, "PNG")
        exec_input_path = temp_transposed_path

    if model_name.startswith("realesr-animevideov3"):
        cli_model = "realesr-animevideov3"
        if model_name == "realesr-animevideov3-x2":
            cli_scale = 2
        elif model_name == "realesr-animevideov3-x3":
            cli_scale = 3
        elif model_name == "realesr-animevideov3-x4":
            cli_scale = 4
        else:
            cli_scale = scale
    elif model_name in NATIVE_4X_MODELS:
        cli_model = model_name
        cli_scale = 4
        if scale < 4:
            downsample_needed = True
    else:
        cli_model = "realesrgan-x4plus"
        cli_scale = 4
        if scale < 4:
            downsample_needed = True

    # Intermediate file if downsampling is required
    raw_output_path = (
        output_path.parent / f"_raw_native4x_{output_path.name}"
        if downsample_needed
        else output_path
    )

    cmd = [
        str(REALESRGAN_EXE),
        "-i", str(exec_input_path.resolve()),
        "-o", str(raw_output_path.resolve()),
        "-s", str(cli_scale),
        "-m", str(MODELS_DIR.resolve()),
        "-n", cli_model,
    ]
    if tile_size > 0:
        cmd.extend(["-t", str(tile_size)])
    if gpu_id != "auto":
        cmd.extend(["-g", str(gpu_id)])

    t0 = time.time()
    logger.info("Executing AI upscaler: %s", " ".join(cmd))
    
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REALESRGAN_DIR),
            timeout=1800,  # 30 minutes maximum timeout
        )
    finally:
        if temp_transposed_path and temp_transposed_path.is_file():
            try:
                temp_transposed_path.unlink()
            except Exception:
                pass

    elapsed = round(time.time() - t0, 2)

    if process.returncode != 0 or not raw_output_path.is_file():
        stderr_msg = process.stderr.strip() or process.stdout.strip()
        if raw_output_path.is_file() and raw_output_path != output_path:
            raw_output_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Real-ESRGAN failed with code {process.returncode}: {stderr_msg}"
        )

    # If downsample is needed (e.g. 4x output scaled down to 2x or 3x)
    target_w = orig_w * scale
    target_h = orig_h * scale

    with Image.open(raw_output_path) as raw_img:
        current_w, current_h = raw_img.size
        if downsample_needed or (current_w, current_h) != (target_w, target_h):
            resampled = raw_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            resampled.save(output_path, "PNG", dpi=(300, 300))
            if raw_output_path != output_path:
                raw_output_path.unlink(missing_ok=True)
            new_w, new_h = target_w, target_h
        else:
            if raw_output_path != output_path:
                shutil.move(str(raw_output_path), str(output_path))
            try:
                raw_img.save(output_path, "PNG", dpi=(300, 300))
            except Exception:
                pass
            new_w, new_h = current_w, current_h

    return {
        "success": True,
        "engine": "realesrgan-ncnn-vulkan",
        "model": model_name,
        "scale": scale,
        "elapsed_seconds": elapsed,
        "original_size": [orig_w, orig_h],
        "upscaled_size": [new_w, new_h],
        "output_path": str(output_path),
    }
