from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from requirements_config import DEFAULT_PROFILE


def compute_image_fingerprint(image_or_path: Path | Image.Image | bytes) -> str:
    """
    Computes deterministic SHA-256 fingerprint from image content.
    Independent of filename or directory.
    """
    if isinstance(image_or_path, (str, Path)):
        p = Path(image_or_path)
        with open(p, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    elif isinstance(image_or_path, bytes):
        return hashlib.sha256(image_or_path).hexdigest()
    elif isinstance(image_or_path, Image.Image):
        return hashlib.sha256(image_or_path.tobytes()).hexdigest()
    else:
        raise TypeError(f"Unsupported type for image fingerprint: {type(image_or_path)}")


def seed_from_fingerprint(fingerprint: str) -> int:
    """Derives a positive 32-bit integer seed from a hex fingerprint string."""
    return int(fingerprint[:16], 16) % (2**31 - 1)


def _integral_box_filter(arr: np.ndarray, r: int) -> np.ndarray:
    """
    Computes fast (2*r + 1)x(2*r + 1) box filter over 2D array in pure NumPy.
    Uses 2D integral image (O(1) lookups per pixel, zero external dependencies).
    """
    if r <= 0:
        return arr.astype(np.float32)
    H, W = arr.shape
    padded = np.pad(arr, r, mode="edge")
    # Integral image with leading zeros
    integ = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
    # Sum over window [i, i + 2*r + 1] x [j, j + 2*r + 1]
    box_sum = (
        integ[2 * r + 1 : 2 * r + 1 + H, 2 * r + 1 : 2 * r + 1 + W]
        - integ[:H, 2 * r + 1 : 2 * r + 1 + W]
        - integ[2 * r + 1 : 2 * r + 1 + H, :W]
        + integ[:H, :W]
    )
    return box_sum / float((2 * r + 1) ** 2)


def _filter_small_regions(binary_mask: np.ndarray, min_size: int) -> np.ndarray:
    """
    Removes connected components smaller than min_size from a binary mask.
    Pure NumPy/Python BFS flood-fill (no scipy required).
    """
    H, W = binary_mask.shape
    if min_size <= 1 or not np.any(binary_mask):
        return binary_mask.copy()

    visited = np.zeros((H, W), dtype=bool)
    output = np.zeros((H, W), dtype=bool)
    ys, xs = np.nonzero(binary_mask)

    for y0, x0 in zip(ys, xs):
        if visited[y0, x0]:
            continue
        comp = []
        queue = [(y0, x0)]
        visited[y0, x0] = True
        comp.append((y0, x0))
        head = 0
        while head < len(queue):
            cy, cx = queue[head]
            head += 1
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < H and 0 <= nx < W and binary_mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    queue.append((ny, nx))
                    comp.append((ny, nx))
        if len(comp) >= min_size:
            for py, px in comp:
                output[py, px] = True

    return output


def detect_flat_areas(
    image: Image.Image,
    max_dim: int = DEFAULT_PROFILE.flat_detection_max_px,
    variance_threshold: float = DEFAULT_PROFILE.flat_variance_threshold,
    edge_density_threshold: float = DEFAULT_PROFILE.flat_edge_density_threshold,
    min_region_pixels: int = DEFAULT_PROFILE.flat_min_region_pixels,
    edge_protection_distance: int = DEFAULT_PROFILE.flat_edge_protection_distance,
    target_size: tuple[int, int] | None = None,
) -> tuple[float, np.ndarray, np.ndarray, dict[str, Any]]:
    """
    Analyzes flat regions on a downscaled copy (max_dim <= 1500px).
    Combines:
    1. Local luminance variance / standard deviation
    2. Edge density in local neighborhood
    3. Connected-region size filtering (ignoring small isolated flat spots)
    4. Edge protection with smooth fadeout near strong edges

    Returns:
    - flat_ratio: float (ratio of flat area to total area on analysis copy)
    - full_mask: np.ndarray (float32 in [0, 1] at target_size or original size, smoothly feathered)
    - analysis_mask: np.ndarray (float32 in [0, 1] at analysis resolution)
    - stats: dict[str, Any] diagnostic information
    """
    orig_w, orig_h = image.size
    final_w, final_h = target_size if target_size else (orig_w, orig_h)

    # 1. Downscale to analysis copy (max ~1500px)
    scale = min(1.0, float(max_dim) / max(orig_w, orig_h))
    an_w = max(16, int(round(orig_w * scale)))
    an_h = max(16, int(round(orig_h * scale)))
    an_img = image.resize((an_w, an_h), Image.Resampling.BILINEAR)

    # 2. Local luminance variance
    gray_img = an_img.convert("L")
    lum = np.asarray(gray_img, dtype=np.float32)

    # Use local patch radius = 5 (window 11x11)
    radius = 5
    mean_lum = _integral_box_filter(lum, radius)
    mean_sq = _integral_box_filter(lum**2, radius)
    local_var = np.maximum(0.0, mean_sq - mean_lum**2)
    local_std = np.sqrt(local_var)

    # 3. Edge density
    edges_img = gray_img.filter(ImageFilter.FIND_EDGES)
    edge_arr = np.asarray(edges_img, dtype=np.float32)
    # Binary strong edge indicator
    strong_edges = (edge_arr > 24.0).astype(np.float32)
    edge_density = _integral_box_filter(strong_edges, 7)

    # 4. Candidate flat mask
    flat_candidates = (local_std <= variance_threshold) & (edge_density <= edge_density_threshold)

    # 5. Connected-region size filtering: eliminate small isolated patches
    # Scale min_region_pixels proportionally to analysis size if needed
    scaled_min_size = max(50, int(min_region_pixels * (scale**2)))
    # Subsample slightly if analysis resolution is large to keep connected component search instant
    if an_w > 500 or an_h > 500:
        sub_scale = 2
        sub_w, sub_h = an_w // sub_scale, an_h // sub_scale
        sub_cand = flat_candidates[::sub_scale, ::sub_scale]
        sub_min = max(20, scaled_min_size // (sub_scale**2))
        sub_filtered = _filter_small_regions(sub_cand, min_size=sub_min)
        # Upscale filtered candidate back to analysis size via Pillow nearest/box
        sub_img = Image.fromarray((sub_filtered.astype(np.uint8) * 255)).resize(
            (an_w, an_h), Image.Resampling.NEAREST
        )
        flat_filtered = np.asarray(sub_img) > 127
    else:
        flat_filtered = _filter_small_regions(flat_candidates, min_size=scaled_min_size)

    # 6. Edge protection: fade out texture near strong edges
    # Strong edge dilation + smooth blur
    edge_u8 = (strong_edges * 255.0).astype(np.uint8)
    edge_pil = Image.fromarray(edge_u8)
    dilated_size = max(3, int(round(edge_protection_distance * scale)))
    if dilated_size % 2 == 0:
        dilated_size += 1
    dilated_edges = edge_pil.filter(ImageFilter.MaxFilter(size=dilated_size))
    feather_r = max(1.5, dilated_size / 2.0)
    blurred_edges = dilated_edges.filter(ImageFilter.GaussianBlur(radius=feather_r))
    edge_fade = 1.0 - (np.asarray(blurred_edges, dtype=np.float32) / 255.0)
    edge_fade = np.clip(edge_fade, 0.0, 1.0)

    # Apply edge protection to flat regions
    analysis_mask = (flat_filtered.astype(np.float32)) * edge_fade
    # Zero out negligible weights
    analysis_mask[analysis_mask < 0.05] = 0.0

    # Calculate flat coverage ratio (based on confident flat area)
    total_pixels = float(an_w * an_h)
    flat_pixels = float(np.sum(analysis_mask > 0.5))
    flat_ratio = round(flat_pixels / total_pixels, 4)

    # 7. Upsample mask to full resolution with smooth feathering
    mask_u8 = (np.clip(analysis_mask, 0.0, 1.0) * 255.0).astype(np.uint8)
    mask_pil = Image.fromarray(mask_u8)
    full_mask_pil = mask_pil.resize((final_w, final_h), Image.Resampling.BILINEAR)

    # Smooth feathering on full-resolution mask (proportional to upscale factor)
    full_blur_radius = max(2.0, (final_w / float(an_w)) * 1.5)
    full_mask_feathered = full_mask_pil.filter(ImageFilter.GaussianBlur(radius=full_blur_radius))
    full_mask = np.asarray(full_mask_feathered, dtype=np.float32) / 255.0
    full_mask = np.clip(full_mask, 0.0, 1.0)
    full_mask[full_mask < 0.02] = 0.0

    stats = {
        "flat_ratio": flat_ratio,
        "flat_pixels": int(flat_pixels),
        "total_pixels": int(total_pixels),
        "analysis_size": [an_w, an_h],
        "full_size": [final_w, final_h],
        "variance_threshold": variance_threshold,
        "edge_density_threshold": edge_density_threshold,
        "exceeds_threshold": bool(flat_ratio > DEFAULT_PROFILE.flat_area_ratio_threshold),
    }

    return flat_ratio, full_mask, analysis_mask, stats


def generate_multiscale_noise(
    width: int,
    height: int,
    seed: int,
    texture_type: str = "fine_paper",
) -> np.ndarray:
    """
    Generates multi-scale filtered continuous procedural noise.
    - fine_paper: soft multi-scale organic micro-grain (non-repeating).
    - fine_canvas: subtle multi-scale noise with very mild directional/weave anisotropy.
    Operates without tiling or visible grid lines.
    Returns 2D float32 array normalized to approx mean=0, std=1.
    """
    rng = np.random.default_rng(seed)

    # 1. Base high-frequency grain
    n0 = rng.normal(0.0, 1.0, (height, width)).astype(np.float32)
    # Soften single-pixel salt with fast BoxBlur(1)
    n0_u8 = np.clip((n0 + 3.0) / 6.0 * 255.0, 0, 255).astype(np.uint8)
    n0_soft = Image.fromarray(n0_u8).filter(ImageFilter.BoxBlur(1))
    n0 = (np.asarray(n0_soft, dtype=np.float32) / 255.0) * 6.0 - 3.0

    # 2. Medium organic frequency
    h1, w1 = max(4, height // 4), max(4, width // 4)
    n1_low = rng.normal(0.0, 1.0, (h1, w1)).astype(np.float32)
    n1_u8 = np.clip((n1_low + 3.0) / 6.0 * 255.0, 0, 255).astype(np.uint8)
    n1_up = Image.fromarray(n1_u8).resize((width, height), Image.Resampling.BILINEAR)
    n1 = (np.asarray(n1_up, dtype=np.float32) / 255.0) * 6.0 - 3.0

    # 3. Macro organic frequency
    h2, w2 = max(4, height // 16), max(4, width // 16)
    n2_low = rng.normal(0.0, 1.0, (h2, w2)).astype(np.float32)
    n2_u8 = np.clip((n2_low + 3.0) / 6.0 * 255.0, 0, 255).astype(np.uint8)
    n2_up = Image.fromarray(n2_u8).resize((width, height), Image.Resampling.BILINEAR)
    n2 = (np.asarray(n2_up, dtype=np.float32) / 255.0) * 6.0 - 3.0

    base = 0.65 * n0 + 0.25 * n1 + 0.10 * n2

    if texture_type == "fine_canvas":
        # Mild directional weave: subtle horizontal and vertical thread tendencies without grid lines
        h_bias_low = rng.normal(0.0, 1.0, (height, max(4, width // 2))).astype(np.float32)
        h_u8 = np.clip((h_bias_low + 3.0) / 6.0 * 255.0, 0, 255).astype(np.uint8)
        h_bias = (np.asarray(Image.fromarray(h_u8).resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0) * 6.0 - 3.0

        v_bias_low = rng.normal(0.0, 1.0, (max(4, height // 2), width)).astype(np.float32)
        v_u8 = np.clip((v_bias_low + 3.0) / 6.0 * 255.0, 0, 255).astype(np.uint8)
        v_bias = (np.asarray(Image.fromarray(v_u8).resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0) * 6.0 - 3.0

        weave = 0.5 * h_bias + 0.5 * v_bias
        base = 0.70 * base + 0.30 * weave

    mean_val = float(base.mean())
    std_val = float(base.std())
    if std_val < 1e-6:
        std_val = 1.0
    norm = (base - mean_val) / std_val
    return norm.astype(np.float32)


def apply_luminance_texture(
    image: Image.Image,
    mask: np.ndarray,
    amplitude: float = 0.015,
    seed: int = 12345,
    texture_type: str = "fine_paper",
) -> tuple[Image.Image, dict[str, Any]]:
    """
    Applies procedural texture exclusively to the luminance channel (YCbCr Y).
    Guarantees:
    - Chroma (Cb, Cr) is never modified intentionally.
    - Pixels where mask == 0 remain strictly bit-identical to the original in-memory.
    - Alpha channel (if present) is extracted and re-attached intact.
    - ICC profile is preserved.
    - Clipping is smoothly prevented.
    """
    orig_icc = image.info.get("icc_profile")
    has_alpha = image.mode in ("RGBA", "LA") or ("A" in image.getbands())
    alpha_channel = image.getchannel("A") if has_alpha else None

    # Base RGB image
    rgb_img = image.convert("RGB")
    orig_rgb_arr = np.asarray(rgb_img)
    w, h = rgb_img.size

    # Fast bail-out if mask has no active pixels or amplitude is zero
    if not np.any(mask > 0.001) or amplitude <= 0:
        result = image.copy()
        if orig_icc:
            result.info["icc_profile"] = orig_icc
        return result, {"applied": False, "reason": "empty_mask_or_zero_amplitude"}

    # 1. Convert to YCbCr
    ycbcr = rgb_img.convert("YCbCr")
    y_band, cb_band, cr_band = ycbcr.split()
    y_arr = np.asarray(y_band, dtype=np.float32)

    # 2. Generate multi-scale noise
    noise = generate_multiscale_noise(width=w, height=h, seed=seed, texture_type=texture_type)

    # 3. Apply perturbation to Y channel:
    # Amplitude is relative to 255.0 (e.g. 0.015 * 255 ~= 3.825 luminance delta)
    delta_y = noise * (amplitude * 255.0) * mask
    new_y = np.clip(y_arr + delta_y, 0.0, 255.0).astype(np.uint8)

    # 4. Re-combine YCbCr with untouched Cb and Cr
    textured_ycbcr = Image.merge("YCbCr", (Image.fromarray(new_y), cb_band, cr_band))
    textured_rgb = textured_ycbcr.convert("RGB")
    textured_rgb_arr = np.asarray(textured_rgb)

    # 5. In-memory bit-identical mask preservation:
    # For every pixel where mask == 0, strictly preserve original RGB values
    final_rgb_arr = textured_rgb_arr.copy()
    zero_mask = (mask == 0.0)
    final_rgb_arr[zero_mask] = orig_rgb_arr[zero_mask]

    out_img = Image.fromarray(final_rgb_arr)

    # 6. Re-attach Alpha if present
    if alpha_channel is not None:
        out_img.putalpha(alpha_channel)

    # 7. Preserve ICC profile
    if orig_icc:
        out_img.info["icc_profile"] = orig_icc

    stats = {
        "applied": True,
        "texture_type": texture_type,
        "amplitude": amplitude,
        "seed": seed,
        "mean_y_delta": float(np.mean(np.abs(delta_y[mask > 0])) if np.any(mask > 0) else 0.0),
        "max_y_delta": float(np.max(np.abs(delta_y))),
    }

    return out_img, stats


def generate_heatmap_preview(
    image: Image.Image,
    mask: np.ndarray,
    output_path: Path | None = None,
    max_preview_dim: int = 1200,
) -> Image.Image:
    """
    Renders a translucent heatmap overlay showing detected flat areas.
    Cyan-to-Orange highlight overlaid on a preview copy of the artwork.
    Saves to output_path if provided.
    """
    w, h = image.size
    preview_scale = min(1.0, float(max_preview_dim) / max(w, h))
    pw = max(16, int(round(w * preview_scale)))
    ph = max(16, int(round(h * preview_scale)))

    preview_img = image.convert("RGB").resize((pw, ph), Image.Resampling.BILINEAR)
    base_arr = np.asarray(preview_img, dtype=np.float32)

    # Resize mask to preview size
    mask_u8 = (np.clip(mask, 0.0, 1.0) * 255.0).astype(np.uint8)
    mask_preview = np.asarray(
        Image.fromarray(mask_u8).resize((pw, ph), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ) / 255.0

    # Overlay color: warm amber/cyan blend (e.g. RGB: [255, 120, 30] for flat areas)
    # Blend with alpha up to 0.45
    overlay_r = 255.0
    overlay_g = 125.0
    overlay_b = 30.0

    alpha = mask_preview[:, :, np.newaxis] * 0.45
    blended = base_arr * (1.0 - alpha) + np.array([overlay_r, overlay_g, overlay_b]) * alpha
    result = Image.fromarray(np.clip(blended, 0.0, 255.0).astype(np.uint8))

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(output_path, "PNG")

    return result
