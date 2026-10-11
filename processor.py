from __future__ import annotations

import enum
import io
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageOps, ImageStat
try:
    import pillow_avif
except Exception:
    pillow_avif = None

from requirements_config import DEFAULT_PROFILE
from status_model import (
    CheckCategory,
    CheckResult,
    CheckStatus,
    DisplateReport,
    OverallStatus,
    ThresholdType,
    aggregate_overall_status,
)
from texture_engine import (
    compute_image_fingerprint,
    seed_from_fingerprint,
    detect_flat_areas,
    apply_luminance_texture,
    generate_heatmap_preview,
)

# ── Resize Modes for Preparation Pipeline ──────────────────────────────────────
class ResizeMode(str, enum.Enum):
    PRESERVE_RESOLUTION = "preserve_resolution"  # Mode A (Default): never downscale solely because > recommended
    RECOMMENDED_SIZE = "recommended_size"        # Mode B: explicit user choice to resize to recommended (4000x5600), never upscale
    SIZE_OPTIMIZATION = "size_optimization"      # Mode C: resize only if needed to achieve <= 30 MB

# ── Centralized Requirements & Configuration (Profile: displate_artist_2026) ──
PROFILE = DEFAULT_PROFILE

# Official requirements
MIN_SHORT         = PROFILE.minimum_short_side_px
TARGET_RATIO      = PROFILE.target_aspect_ratio
MIN_DPI           = PROFILE.minimum_dpi
MAX_MB            = PROFILE.maximum_file_size_mb
MAX_FILE_BYTES    = PROFILE.maximum_file_size_bytes  # Binary MB: 30 * 1024 * 1024 = 31,457,280 bytes

# Official reference (associated with 4060x2900 reference file)
SAFE_MARGIN       = PROFILE.reference_safe_margin_px

# Recommendations
RECOMMENDED_SHORT = PROFILE.portrait_width_px
RECOMMENDED_LONG  = PROFILE.portrait_height_px

def is_dpi_valid(dpi_val: float) -> bool:
    """Displate requires DPI >= 300. 600 DPI is fully valid."""
    return round(float(dpi_val), 1) >= float(MIN_DPI)

# Internal computational / heuristic thresholds (NOT official Displate thresholds)
RATIO_TOL         = PROFILE.aspect_ratio_comparison_epsilon
BORDER_SAMPLE_PX  = PROFILE.border_sample_px
BORDER_DIFF_THR   = PROFILE.border_difference_threshold
NOISE_FLAT_THR    = PROFILE.noise_flat_threshold
NOISE_WARN_STD    = PROFILE.noise_warn_std
BRIGHTNESS_DARK_THR = PROFILE.brightness_dark_threshold
UPSCALE_VAR_THR   = PROFILE.upscale_var_threshold
UPSCALE_EDGE_THR  = PROFILE.upscale_edge_threshold
SHARP_RATIO_THR   = PROFILE.sharp_ratio_threshold
SHARP_ABS_THR     = PROFILE.sharp_abs_threshold
EDGE_CONTENT_ABS  = PROFILE.edge_content_abs
EDGE_CONTENT_RATIO = PROFILE.edge_content_ratio
FLAT_AREA_RATIO_THR = PROFILE.flat_area_ratio_threshold

# ── DEPRECATED: Arbitrary risk scoring removed in Phase 2 ──────────────────────
# Retained as empty stubs only for backward compatibility if imported externally
RISK_PENALTIES: dict[str, dict[str, int]] = {}
BASE_RISK = 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _status(code: str, label: str, status: str, message: str, value=None) -> dict:
    return {"code": code, "label": label, "status": status, "message": message, "value": value}


def _srgb_icc() -> bytes:
    profile = ImageCms.createProfile("sRGB")
    return ImageCms.ImageCmsProfile(profile).tobytes()


def _profile_name(image: Image.Image) -> str:
    icc = image.info.get("icc_profile")
    if not icc:
        return "No embedded profile"
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return ImageCms.getProfileName(profile).strip() or "Embedded profile"
    except Exception:
        return "Embedded profile"


def _dpi(image: Image.Image):
    dpi = image.info.get("dpi")
    if isinstance(dpi, (tuple, list)) and len(dpi) >= 2:
        try:
            return round(float(dpi[0]), 2), round(float(dpi[1]), 2)
        except Exception:
            pass
    return 0.0, 0.0


def _sharpness_heuristic(image: Image.Image) -> dict[str, float | str]:
    preview = ImageOps.exif_transpose(image).convert("RGB")
    preview.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    gray  = preview.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    stat  = ImageStat.Stat(edges)
    rms   = float(stat.rms[0])
    mean  = float(stat.mean[0])
    level = "soft" if rms < 18 else "moderate" if rms < 27 else "detailed"
    return {"edge_rms": round(rms, 2), "edge_mean": round(mean, 2), "level": level}


def _crop_box_to_ratio(width: int, height: int, focus: str = "center"):
    portrait = height >= width
    if portrait:
        current = height / width
        if abs(current - TARGET_RATIO) <= RATIO_TOL:
            return (0, 0, width, height), "none"
        if current > TARGET_RATIO:
            target_h = min(int(round(width * TARGET_RATIO)), height)
            extra = height - target_h
            top = 0 if focus == "top" else extra if focus == "bottom" else extra // 2
            return (0, top, width, top + target_h), "height"
        target_w = min(int(round(height / TARGET_RATIO)), width)
        extra = width - target_w
        left = 0 if focus == "left" else extra if focus == "right" else extra // 2
        return (left, 0, left + target_w, height), "width"

    current = width / height
    if abs(current - TARGET_RATIO) <= RATIO_TOL:
        return (0, 0, width, height), "none"
    if current > TARGET_RATIO:
        target_w = min(int(round(height * TARGET_RATIO)), width)
        extra = width - target_w
        left = 0 if focus == "left" else extra if focus == "right" else extra // 2
        return (left, 0, left + target_w, height), "width"
    target_h = min(int(round(width / TARGET_RATIO)), height)
    extra = height - target_h
    top = 0 if focus == "top" else extra if focus == "bottom" else extra // 2
    return (0, top, width, top + target_h), "height"


# ── Check 1 helper: size badge ─────────────────────────────────────────────────

def _size_badge(short_side: int, long_side: int) -> str:
    """Returns 'optimal', 'minimum', or 'blocked'."""
    if short_side < MIN_SHORT:
        return "blocked"
    if short_side >= RECOMMENDED_SHORT and long_side >= RECOMMENDED_LONG:
        return "optimal"
    return "minimum"


# ── Check 8: border / frame (per-side) ────────────────────────────────────────

def _detect_border(image: Image.Image) -> dict[str, Any]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    px = min(BORDER_SAMPLE_PX, w // 10, h // 10)
    if px < 4:
        return {"has_border": False, "confidence": "low", "sides": [], "details": "Image too small for border check."}

    def mean_color(region):
        stat = ImageStat.Stat(region)
        return stat.mean[:3]

    def color_dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

    center = rgb.crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4))
    mc     = mean_color(center)

    side_crops = {
        "top":    rgb.crop((0,      0,      w,      px)),
        "bottom": rgb.crop((0,      h - px, w,      h)),
        "left":   rgb.crop((0,      0,      px,     h)),
        "right":  rgb.crop((w - px, 0,      w,      h)),
    }
    dists        = {k: color_dist(mean_color(v), mc) for k, v in side_crops.items()}
    border_sides = [k for k, d in dists.items() if d > BORDER_DIFF_THR]
    has_border   = bool(border_sides)
    max_dist     = max(dists.values())
    confidence   = "high" if max_dist > BORDER_DIFF_THR * 1.8 else "medium" if has_border else "low"

    return {
        "has_border":    has_border,
        "confidence":    confidence,
        "max_edge_diff": round(max_dist, 1),
        "sides":         border_sides,
        "dists":         {k: round(v, 1) for k, v in dists.items()},
        "details": (
            f"Border detected on: {', '.join(border_sides)}."
            if has_border else "No obvious border/frame detected."
        ),
    }


# ── Check 9: noise / grain ────────────────────────────────────────────────────

def _noise_heuristic(image: Image.Image) -> dict[str, Any]:
    rgb  = image.convert("RGB")
    w, h = rgb.size
    size  = min(300, w // 4, h // 4)
    patch = rgb.crop((0, h - size, size, h)).convert("L")
    stat  = ImageStat.Stat(patch)
    std   = float(stat.stddev[0])
    level = "clean" if std < NOISE_FLAT_THR else "moderate" if std < NOISE_WARN_STD else "heavy"
    return {"std": round(std, 2), "level": level}


# ── Check 10: EXIF dimension consistency ──────────────────────────────────────

def _exif_check(image: Image.Image, actual_w: int, actual_h: int) -> dict[str, Any]:
    try:
        exif  = image.getexif()
        exif_w = exif.get(0xA002)
        exif_h = exif.get(0xA003)
        if exif_w and exif_h:
            mismatch = (int(exif_w) != actual_w or int(exif_h) != actual_h)
            return {
                "exif_w": int(exif_w), "exif_h": int(exif_h),
                "mismatch": mismatch,
                "details": (
                    f"EXIF says {exif_w}×{exif_h}px but actual is {actual_w}×{actual_h}px — "
                    "possible resampling detected." if mismatch
                    else "EXIF dimensions match actual size."
                ),
            }
    except Exception:
        pass
    return {"exif_w": None, "exif_h": None, "mismatch": False, "details": "No EXIF dimension data found."}


# ── NEW Check 12: brightness / dark composition ───────────────────────────────

def _brightness_check(image: Image.Image) -> dict[str, Any]:
    """
    Checks global luminance. Very dark images tend to be rejected by Displate.
    Returns mean luminance (0-255) and pixel distribution (dark/mid/bright %).
    """
    preview = image.copy()
    preview.thumbnail((800, 800), Image.Resampling.LANCZOS)
    gray = preview.convert("L")
    stat = ImageStat.Stat(gray)
    mean = float(stat.mean[0])
    hist = gray.histogram()   # 256 buckets
    total = sum(hist)
    dark_pct   = round(sum(hist[:85])     / total * 100, 1)
    mid_pct    = round(sum(hist[85:170])  / total * 100, 1)
    bright_pct = round(sum(hist[170:])    / total * 100, 1)
    return {
        "mean":        round(mean, 2),
        "dark_pct":    dark_pct,
        "mid_pct":     mid_pct,
        "bright_pct":  bright_pct,
        "too_dark":    mean < BRIGHTNESS_DARK_THR,
    }


# ── NEW Check 13: upscale / artificial enlargement detection ──────────────────

def _upscale_detection(image: Image.Image) -> dict[str, Any]:
    """
    Analyses local variance and edge density to detect possible artificial upscaling.
    Bicubic/bilinear-upscaled images have very low, uniform local variance + soft edges.
    Vector art also has low variance but has SHARP edges (high RMS / mean ratio).
    """
    preview = image.copy().convert("L")
    preview.thumbnail((512, 512), Image.Resampling.LANCZOS)
    w, h = preview.size

    # Sample 16 blocks (4×4 grid) and compute variance per block
    bw, bh = w // 4, h // 4
    variances = []
    for row in range(4):
        for col in range(4):
            box    = (col * bw, row * bh, (col + 1) * bw, (row + 1) * bh)
            region = preview.crop(box)
            s      = ImageStat.Stat(region)
            variances.append(float(s.stddev[0]) ** 2)

    mean_var  = sum(variances) / len(variances)

    # Edge density & sharpness
    edges      = preview.filter(ImageFilter.FIND_EDGES)
    edge_stat  = ImageStat.Stat(edges)
    edge_mean  = float(edge_stat.mean[0])
    edge_rms   = float(edge_stat.rms[0])
    # High rms/mean ratio = sharp clean edges (vector art, NOT upscale)
    sharpness_ratio = edge_rms / (edge_mean + 0.001)

    # Suspected if globally smooth AND edges are soft (not vector art)
    suspected  = mean_var < UPSCALE_VAR_THR and edge_mean < UPSCALE_EDGE_THR and sharpness_ratio < 2.0
    confidence = "high" if mean_var < 25 and edge_mean < 5 else "medium" if suspected else "low"

    return {
        "suspected":      suspected,
        "confidence":     confidence,
        "mean_local_var": round(mean_var, 2),
        "edge_density":   round(edge_mean, 2),
        "sharpness_ratio": round(sharpness_ratio, 3),
    }


# ── NEW Check 14: over-sharpening / halo filter detection ────────────────────

def _over_sharpening_check(image: Image.Image) -> dict[str, Any]:
    """
    Detects excessive unsharp-mask or sharpening filters by comparing edge RMS
    of the original vs a slightly blurred version. A very high ratio indicates
    artificial sharpening (halo artifacts around edges).
    """
    preview = image.copy().convert("L")
    preview.thumbnail((600, 600), Image.Resampling.LANCZOS)

    edges_orig  = preview.filter(ImageFilter.FIND_EDGES)
    edges_blur  = preview.filter(ImageFilter.GaussianBlur(radius=1)).filter(ImageFilter.FIND_EDGES)

    rms_orig  = float(ImageStat.Stat(edges_orig).rms[0])
    rms_blur  = float(ImageStat.Stat(edges_blur).rms[0])
    ratio     = rms_orig / (rms_blur + 0.001)

    over_sharpened = ratio > SHARP_RATIO_THR and rms_orig > SHARP_ABS_THR

    return {
        "edge_rms":       round(rms_orig, 2),
        "ratio":          round(ratio, 3),
        "over_sharpened": over_sharpened,
    }


# ── NEW Check 15: edge-content proximity ─────────────────────────────────────

def _edge_content_check(image: Image.Image, margin: int = SAFE_MARGIN) -> dict[str, Any]:
    """
    Detects high-frequency content (text, logos, signatures) in the 200px
    safe-margin zone by comparing edge density of border strips vs image centre.
    """
    small = image.copy().convert("RGB")
    scale = 0.25
    small = small.resize(
        (max(80, int(image.width * scale)), max(80, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    w, h = small.size
    eff  = max(8, int(margin * scale))

    gray  = small.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)

    def strip_edge_mean(box):
        return float(ImageStat.Stat(edges.crop(box)).mean[0])

    centre_mean = strip_edge_mean((w // 4, h // 4, 3 * w // 4, 3 * h // 4))
    strips = {
        "top":    (0,         0,         w,         eff),
        "bottom": (0,         h - eff,   w,         h),
        "left":   (0,         0,         eff,       h),
        "right":  (w - eff,   0,         w,         h),
    }
    strip_means  = {k: strip_edge_mean(v) for k, v in strips.items()}
    risky_edges  = [
        k for k, m in strip_means.items()
        if m > EDGE_CONTENT_ABS and m > centre_mean * EDGE_CONTENT_RATIO
    ]

    return {
        "strip_means":  {k: round(v, 1) for k, v in strip_means.items()},
        "centre_mean":  round(centre_mean, 1),
        "risky_edges":  risky_edges,
        "margin_px":    margin,
    }


# ── Risk Score ────────────────────────────────────────────────────────────────

# ── DEPRECATED: Risk Score (Removed in Phase 2) ───────────────────────────────

def compute_risk_score(checks: Any = None) -> dict[str, Any]:
    """
    DEPRECATED: Numerical risk scoring is removed in Phase 2.
    Provided for backwards compatibility only.
    """
    return {"score": None, "level": "none", "breakdown": []}


# ── Core: analyze ──────────────────────────────────────────────────────────────

def analyze(path: Path, focus: str = "center", upscale_applied: dict | None = None) -> dict[str, Any]:
    file_mb = path.stat().st_size / (1024 * 1024)
    with Image.open(path) as raw:
        image   = ImageOps.exif_transpose(raw)
        width, height = image.size
        dpi     = _dpi(image)
        profile = _profile_name(image)
        sharp   = _sharpness_heuristic(image)
        border  = _detect_border(image)
        noise   = _noise_heuristic(image)
        exif_r  = _exif_check(image, width, height)
        bright  = _brightness_check(image)
        upscale = _upscale_detection(image)
        oversharp = _over_sharpening_check(image)
        edge_c  = _edge_content_check(image)
        flat_ratio, _, _, flat_stats = detect_flat_areas(
            image,
            max_dim=PROFILE.flat_detection_max_px,
            variance_threshold=PROFILE.flat_variance_threshold,
            edge_density_threshold=PROFILE.flat_edge_density_threshold,
            min_region_pixels=PROFILE.flat_min_region_pixels,
            edge_protection_distance=PROFILE.flat_edge_protection_distance,
        )

    orientation  = "portrait" if height >= width else "landscape"
    short_side   = min(width, height)
    long_side    = max(width, height)
    size_badge   = _size_badge(short_side, long_side)

    crop_box, crop_kind = _crop_box_to_ratio(width, height, focus)
    crop_w   = crop_box[2] - crop_box[0]
    crop_h   = crop_box[3] - crop_box[1]
    post_short = min(crop_w, crop_h)

    # Size status message
    if size_badge == "optimal":
        size_msg = f"Source: {width}×{height}px — Optimal quality (≥4000×5600)."
    elif size_badge == "minimum":
        size_msg = f"Source: {width}×{height}px — Meets minimum (≥2900px). Recommend 4000×5600 for best print quality."
    else:
        size_msg = f"Source: {width}×{height}px — Shortest side {short_side}px is below the 2900px minimum."

    # 1. Official Requirements (Published hard technical boundaries)
    dpi_pass = (is_dpi_valid(dpi[0]) and is_dpi_valid(dpi[1]))
    official_checks = [
        CheckResult(
            id="minimum_short_side",
            name="Minimum Short Side",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.PASS if short_side >= MIN_SHORT else CheckStatus.FAIL,
            value=short_side,
            unit="px",
            threshold=MIN_SHORT,
            threshold_type=ThresholdType.OFFICIAL,
            operator=">=",
            message=size_msg,
            details={"width": width, "height": height, "badge": size_badge},
        ),
        CheckResult(
            id="post_crop_size",
            name="Resolution After 5:7 Crop",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.PASS if post_short >= MIN_SHORT else CheckStatus.FAIL,
            value=post_short,
            unit="px",
            threshold=MIN_SHORT,
            threshold_type=ThresholdType.OFFICIAL,
            operator=">=",
            message=f"After crop: {crop_w}×{crop_h}px — shortest side {post_short}px.",
            details={"crop_w": crop_w, "crop_h": crop_h},
        ),
        CheckResult(
            id="target_aspect_ratio",
            name="5:7 / 1.4:1 Aspect Ratio",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.PASS if crop_kind == "none" else CheckStatus.WARNING,
            value=round(max(width, height) / min(width, height), 3),
            unit="ratio",
            threshold=TARGET_RATIO,
            threshold_type=ThresholdType.OFFICIAL,
            operator="==",
            message="Already 5:7 — no crop needed." if crop_kind == "none" else f"Crop required in the {crop_kind} direction (before: {width}×{height}, after: {crop_w}×{crop_h}).",
            details={"crop_kind": crop_kind, "before": [width, height], "after": [crop_w, crop_h]},
        ),
        CheckResult(
            id="minimum_dpi",
            name="Minimum 300 DPI",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.PASS if dpi_pass else CheckStatus.FAIL,
            value=list(dpi),
            unit="dpi",
            threshold=MIN_DPI,
            threshold_type=ThresholdType.OFFICIAL,
            operator=">=",
            message=f"Current DPI: {dpi[0]}×{dpi[1]}." if dpi_pass else f"Current DPI {dpi[0]}×{dpi[1]} is below minimum 300 DPI.",
            details={"dpi_x": dpi[0], "dpi_y": dpi[1]},
        ),
        CheckResult(
            id="maximum_file_size",
            name="Maximum File Size (30 MB)",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.PASS if file_mb <= MAX_MB else CheckStatus.WARNING,
            value=round(file_mb, 2),
            unit="MB",
            threshold=MAX_MB,
            threshold_type=ThresholdType.OFFICIAL,
            operator="<=",
            message=f"Current size: {file_mb:.2f} MB. Final export will target ≤30 MB.",
            details={"file_mb": round(file_mb, 3)},
        ),
        CheckResult(
            id="reference_safe_margin",
            name="Official Safe-Margin Reference (200px)",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.WARNING if edge_c["risky_edges"] else CheckStatus.PASS,
            value=SAFE_MARGIN,
            unit="px",
            threshold=SAFE_MARGIN,
            threshold_type=ThresholdType.OFFICIAL_REFERENCE,
            message=(
                f"High-detail content detected within {SAFE_MARGIN}px reference margin on edge(s): {', '.join(edge_c['risky_edges'])} — check preview."
                if edge_c["risky_edges"]
                else f"No critical content detected within {SAFE_MARGIN}px reference margin."
            ),
            details={
                "reference_short_side_px": PROFILE.reference_short_side_px,
                "reference_file_dimensions": "4060x2900",
                "risky_edges": edge_c["risky_edges"],
                "strip_means": edge_c["strip_means"],
            },
        ),
    ]

    # 2. Recommendations (Best practices; not hard rejection boundaries)
    norm_profile = profile.lower()
    profile_recognized = (
        "srgb" in norm_profile
        or "adobergb" in norm_profile
        or "adobe rgb" in norm_profile
        or "display p3" in norm_profile
    )
    rec_checks = [
        CheckResult(
            id="recommended_dimensions",
            name="Recommended Dimensions (4000×5600)",
            category=CheckCategory.RECOMMENDATIONS,
            status=CheckStatus.PASS if (short_side >= RECOMMENDED_SHORT and long_side >= RECOMMENDED_LONG) else CheckStatus.WARNING,
            value=f"{width}x{height}",
            unit="px",
            threshold=f"{RECOMMENDED_SHORT}x{RECOMMENDED_LONG}",
            threshold_type=ThresholdType.RECOMMENDATION,
            message="Source meets or exceeds recommended 4000×5600 dimensions." if (short_side >= RECOMMENDED_SHORT and long_side >= RECOMMENDED_LONG) else "Below recommended 4000×5600 px (acceptable if shortest side ≥2900 px).",
            details={"badge": size_badge},
        ),
        CheckResult(
            id="preferred_color_profile",
            name="Color Profile (sRGB Recommended)",
            category=CheckCategory.RECOMMENDATIONS,
            status=CheckStatus.PASS if profile_recognized else CheckStatus.WARNING,
            value=profile,
            threshold="sRGB",
            threshold_type=ThresholdType.RECOMMENDATION,
            message=f"Current profile: {profile}." + (" (Official sRGB normalization recommended)." if "srgb" not in norm_profile else " (Matches recommended sRGB)."),
            details={"profile": profile},
        ),
        CheckResult(
            id="orientation",
            name="Artwork Orientation",
            category=CheckCategory.RECOMMENDATIONS,
            status=CheckStatus.PASS,
            value=orientation,
            threshold_type=ThresholdType.NONE,
            message=f"{orientation.capitalize()} orientation ({width}×{height}px).",
            details={"orientation": orientation},
        ),
    ]

    # 3. Internal Heuristics (Preventive indicators; CANNOT produce BLOCKED)
    heuristic_checks = [
        CheckResult(
            id="sharpness",
            name="Global Sharpness Heuristic",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING if sharp["level"] == "soft" else CheckStatus.PASS,
            value=sharp["edge_rms"],
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message="Source looks globally soft — inspect at 100% zoom." if sharp["level"] == "soft" else f"Global detail level: {sharp['level']}.",
            details=sharp,
        ),
        CheckResult(
            id="border_check",
            name="Artificial Border / Frame Detection",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING if border["has_border"] else CheckStatus.PASS,
            value=border["sides"],
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message=border["details"] + (f" Sides: {', '.join(border['sides'])}." if border.get("sides") else ""),
            details=border,
        ),
        CheckResult(
            id="noise_check",
            name="Noise / Grain Level Heuristic",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING if noise["level"] == "heavy" else CheckStatus.PASS,
            value=noise["std"],
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message=f"Grain level: {noise['level']} (std={noise['std']}).",
            details=noise,
        ),
        CheckResult(
            id="exif_check",
            name="EXIF Dimension Consistency",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING if exif_r["mismatch"] else CheckStatus.PASS,
            value=exif_r["mismatch"],
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message=exif_r["details"],
            details=exif_r,
        ),
        CheckResult(
            id="brightness",
            name="Brightness / Dark Composition Heuristic",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING if bright["too_dark"] else CheckStatus.PASS,
            value=bright["mean"],
            threshold=PROFILE.brightness_dark_threshold,
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message=f"Mean luminance {bright['mean']}/255 is very dark; review for metal print contrast." if bright["too_dark"] else f"Mean luminance: {bright['mean']}/255.",
            details=bright,
        ),
        CheckResult(
            id="upscale_detect",
            name="Artificial Upscaling Detection",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.PASS if upscale_applied else (CheckStatus.WARNING if upscale["suspected"] else CheckStatus.PASS),
            value=upscale["mean_local_var"],
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message=(
                f"AI Super-Resolution applied via {upscale_applied.get('model', 'Real-ESRGAN')} ({upscale_applied.get('scale', 4)}x) — inspect at 100% zoom."
                if upscale_applied
                else (
                    f"Possible artificial upscaling detected (confidence: {upscale['confidence']}) — local variance low ({upscale['mean_local_var']})."
                    if upscale["suspected"]
                    else f"No upscaling detected (local variance: {upscale['mean_local_var']})."
                )
            ),
            details={**upscale, "upscale_applied": upscale_applied} if upscale_applied else upscale,
        ),
        CheckResult(
            id="over_sharpening",
            name="Over-Sharpening / Halo Filter Detection",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING if oversharp["over_sharpened"] else CheckStatus.PASS,
            value=oversharp["ratio"],
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            message=f"Possible sharpening halo artifacts detected (edge ratio {oversharp['ratio']}×)." if oversharp["over_sharpened"] else f"No excessive sharpening detected (ratio: {oversharp['ratio']}×).",
            details=oversharp,
        ),
        CheckResult(
            id="flat_area_check",
            name="Large Flat Surface Detection",
            category=CheckCategory.INTERNAL_HEURISTICS,
            status=CheckStatus.WARNING if flat_stats["exceeds_threshold"] else CheckStatus.PASS,
            value=round(flat_ratio * 100, 1),
            unit="%",
            threshold=round(PROFILE.flat_area_ratio_threshold * 100, 1),
            threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            operator="<=",
            message=(
                f"Large flat surface detected (~{round(flat_ratio * 100, 1)}% of canvas) — optional texture enhancement recommended."
                if flat_stats["exceeds_threshold"]
                else f"Flat surface area within normal bounds (~{round(flat_ratio * 100, 1)}% of canvas)."
            ),
            details=flat_stats,
        ),
    ]

    # 4. Manual User Confirmations (Explicit placeholders)
    manual_checks = [
        CheckResult(
            id="ownership_confirmation",
            name="Artwork Ownership & Licensing",
            category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
            status=CheckStatus.NOT_CHECKED,
            threshold_type=ThresholdType.NONE,
            message="User confirmation required: I own or have valid license for this artwork.",
        ),
        CheckResult(
            id="commercial_rights_confirmation",
            name="Commercial Distribution Rights",
            category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
            status=CheckStatus.NOT_CHECKED,
            threshold_type=ThresholdType.NONE,
            message="User confirmation required: I have rights to sell this design commercially on Displate.",
        ),
        CheckResult(
            id="whole_design_confirmation",
            name="Complete Design in Single File",
            category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
            status=CheckStatus.NOT_CHECKED,
            threshold_type=ThresholdType.NONE,
            message="User confirmation required: Entire design is included in this single upload file.",
        ),
    ]

    all_checks = official_checks + rec_checks + heuristic_checks + manual_checks
    overall_status, counts = aggregate_overall_status(all_checks)

    report_obj = DisplateReport(
        report_version="1.0",
        tool={
            "name": "Displate One-Click Preparer",
            "version": "1.0.0",
            "requirements_profile": PROFILE.profile_id,
        },
        input={
            "filename": path.name,
            "width": width,
            "height": height,
            "file_mb": round(file_mb, 3),
            "dpi": list(dpi),
            "color_profile": profile,
            "orientation": orientation,
            "size_badge": size_badge,
            "upscaled": bool(upscale_applied),
            "upscale_info": upscale_applied,
        },
        checks={
            "official_requirements": [c.to_dict() for c in official_checks],
            "recommendations": [c.to_dict() for c in rec_checks],
            "internal_heuristics": [c.to_dict() for c in heuristic_checks],
            "manual_user_confirmations": [c.to_dict() for c in manual_checks],
        },
        processing={
            "orientation_normalized": True,
            "crop": {
                "performed": crop_kind != "none",
                "mode": focus,
                "box": list(crop_box),
                "size": [crop_w, crop_h],
                "original_size": [width, height],
                "original_ratio": round(max(width, height) / min(width, height), 4),
                "target_ratio": TARGET_RATIO,
                "crop_kind": crop_kind,
            },
            "crop_box": list(crop_box),
            "crop_size": [crop_w, crop_h],
            "crop_kind": crop_kind,
            "focus": focus,
        },
        overall={
            "status": overall_status.value,
            **counts,
        },
    )
    return report_obj.to_dict()


# ── Core: Phase 3 Processing Pipeline Helpers ─────────────────────────────────

def _convert_to_srgb(image: Image.Image, normalize: bool = True) -> tuple[Image.Image, dict[str, Any]]:
    """
    Performs ICC-aware color conversion to sRGB.
    If image has an embedded ICC profile (e.g. AdobeRGB, Display P3, CMYK),
    converts pixels using LittleCMS to sRGB.
    If untagged, normalizes/tags as sRGB safely.
    Returns (converted_image, conversion_record).
    """
    src_profile_name = _profile_name(image)
    if not normalize:
        return image, {
            "performed": False,
            "source_profile": src_profile_name,
            "destination_profile": src_profile_name,
            "profile_embedded": bool(image.info.get("icc_profile")),
            "conversion_type": "none",
        }

    icc = image.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            work_img = image
            if work_img.mode == "RGBA":
                bg = Image.new("RGB", work_img.size, (255, 255, 255))
                bg.paste(work_img, mask=work_img.split()[3])
                work_img = bg
            elif work_img.mode != "RGB":
                work_img = work_img.convert("RGB")

            converted = ImageCms.profileToProfile(
                work_img, src, dst, outputMode="RGB",
                renderingIntent=ImageCms.Intent.PERCEPTUAL,
            )
            converted.info["icc_profile"] = _srgb_icc()
            return converted, {
                "performed": True,
                "source_profile": src_profile_name,
                "destination_profile": "sRGB",
                "profile_embedded": True,
                "conversion_type": "icc_transform",
            }
        except Exception as exc:
            work_img = image.convert("RGB")
            work_img.info["icc_profile"] = _srgb_icc()
            return work_img, {
                "performed": False,
                "source_profile": src_profile_name,
                "destination_profile": "sRGB",
                "profile_embedded": True,
                "conversion_type": "fallback_convert_rgb",
                "warning": f"ICC transform failed: {exc}",
            }

    # No embedded ICC profile
    work_img = image
    if work_img.mode == "RGBA":
        bg = Image.new("RGB", work_img.size, (255, 255, 255))
        bg.paste(work_img, mask=work_img.split()[3])
        work_img = bg
    elif work_img.mode != "RGB":
        work_img = work_img.convert("RGB")

    work_img.info["icc_profile"] = _srgb_icc()
    return work_img, {
        "performed": False,
        "source_profile": "No embedded profile",
        "destination_profile": "sRGB",
        "profile_embedded": True,
        "conversion_type": "tagged_as_srgb",
        "note": "Source image had no embedded profile; normalized to sRGB",
    }


def _apply_resize_mode(
    image: Image.Image,
    mode: str | ResizeMode = ResizeMode.RECOMMENDED_SIZE,
    was_upscaled: bool = False,
) -> tuple[Image.Image, dict[str, Any]]:
    """
    Applies explicit resize mode:
    - RECOMMENDED_SIZE (Default): Resizes to Displate's official optimal dimensions
      (4000x5600 for portrait, 5600x4000 for landscape) using high-quality Lanczos resampling.
    - PRESERVE_RESOLUTION: Keeps original resolution (as long as shortest side >= 2900 px).
      Note: If image was AI upscaled, it is always standardized to Displate 4000x5600.
    - SIZE_OPTIMIZATION: Preserves dimensions initially; downscales only if JPEG exceeds 30 MB.
    """
    if isinstance(mode, str):
        try:
            mode = ResizeMode(mode)
        except ValueError:
            mode = ResizeMode.RECOMMENDED_SIZE

    w, h = image.size
    is_portrait = h >= w
    orig_size = [w, h]

    if (mode == ResizeMode.RECOMMENDED_SIZE) or was_upscaled:
        target_w = RECOMMENDED_SHORT if is_portrait else RECOMMENDED_LONG
        target_h = RECOMMENDED_LONG if is_portrait else RECOMMENDED_SHORT
        target = (target_w, target_h)
        if (w, h) != target:
            resized = image.resize(target, Image.Resampling.LANCZOS)
            return resized, {
                "performed": True,
                "mode": mode.value if hasattr(mode, "value") else str(mode),
                "reason": "displate_standard_recommended_size",
                "original_size": orig_size,
                "final_size": list(target),
            }
        return image, {
            "performed": False,
            "mode": mode.value if hasattr(mode, "value") else str(mode),
            "reason": "already_recommended_size",
            "original_size": orig_size,
            "final_size": orig_size,
        }

    if mode == ResizeMode.PRESERVE_RESOLUTION:
        return image, {
            "performed": False,
            "mode": mode.value,
            "reason": "preserve_resolution_requested",
            "original_size": orig_size,
            "final_size": orig_size,
        }

    # SIZE_OPTIMIZATION: initial pass preserves dimensions
    return image, {
        "performed": False,
        "mode": mode.value,
        "reason": None,
        "original_size": orig_size,
        "final_size": orig_size,
    }


def _save_jpeg_under_limit(
    image: Image.Image,
    output: Path,
    target_dpi: int = 300,
    icc_bytes: bytes | None = None,
    resize_mode: str | ResizeMode = ResizeMode.PRESERVE_RESOLUTION,
    max_bytes: int = MAX_FILE_BYTES,
) -> dict[str, Any]:
    """
    Iterative JPEG export and file size optimization targeting <= 30 MB (max_bytes).
    First tests quality degradation down to quality floor (70).
    If still oversized and resize_mode == SIZE_OPTIMIZATION, iteratively downscales
    without ever dropping shortest side below 2900 px.
    """
    if isinstance(resize_mode, str):
        try:
            resize_mode = ResizeMode(resize_mode)
        except ValueError:
            resize_mode = ResizeMode.PRESERVE_RESOLUTION

    icc = icc_bytes if icc_bytes is not None else _srgb_icc()
    qualities = [96, 92, 88, 85, 82, 78, 75, 70]
    iterations = 0
    work_img = image

    # 1. Quality reduction loop on current dimensions
    for q in qualities:
        iterations += 1
        work_img.save(
            output, "JPEG", quality=q,
            subsampling=0 if q >= 85 else 1,
            optimize=True, progressive=True,
            dpi=(target_dpi, target_dpi), icc_profile=icc,
        )
        actual_bytes = output.stat().st_size
        if actual_bytes <= max_bytes:
            return {
                "success": True,
                "quality": q,
                "initial_quality": 96,
                "iterations": iterations,
                "subsampling": 0 if q >= 85 else 1,
                "target_dpi": target_dpi,
                "resized": False,
                "final_bytes": actual_bytes,
                "final_mb": round(actual_bytes / (1024 * 1024), 3),
                "final_size": list(work_img.size),
            }

    # 2. If still > 30 MB and SIZE_OPTIMIZATION mode is enabled:
    if resize_mode == ResizeMode.SIZE_OPTIMIZATION:
        scale_steps = [0.90, 0.80, 0.70, 0.60, 0.50]
        for scale in scale_steps:
            new_w = int(round(image.width * scale))
            new_h = int(round(image.height * scale))
            # Never reduce short side below MIN_SHORT (2900 px)
            if min(new_w, new_h) < MIN_SHORT:
                if image.height >= image.width:
                    new_w = MIN_SHORT
                    new_h = int(round(MIN_SHORT * (image.height / image.width)))
                else:
                    new_h = MIN_SHORT
                    new_w = int(round(MIN_SHORT * (image.width / image.height)))

            if (new_w, new_h) == work_img.size:
                break

            work_img = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            for q in [85, 80, 75, 70]:
                iterations += 1
                work_img.save(
                    output, "JPEG", quality=q,
                    subsampling=1,
                    optimize=True, progressive=True,
                    dpi=(target_dpi, target_dpi), icc_profile=icc,
                )
                actual_bytes = output.stat().st_size
                if actual_bytes <= max_bytes:
                    return {
                        "success": True,
                        "quality": q,
                        "initial_quality": 96,
                        "iterations": iterations,
                        "subsampling": 1,
                        "target_dpi": target_dpi,
                        "resized": True,
                        "resize_reason": "file_size_optimization",
                        "final_bytes": actual_bytes,
                        "final_mb": round(actual_bytes / (1024 * 1024), 3),
                        "final_size": list(work_img.size),
                    }

    # Could not reach <= 30 MB within constraints
    actual_bytes = output.stat().st_size if output.exists() else 0
    return {
        "success": False,
        "quality": 70,
        "initial_quality": 96,
        "iterations": iterations,
        "subsampling": 1,
        "target_dpi": target_dpi,
        "resized": work_img.size != image.size,
        "final_bytes": actual_bytes,
        "final_mb": round(actual_bytes / (1024 * 1024), 3),
        "final_size": list(work_img.size),
        "error": f"File size ({round(actual_bytes / (1024 * 1024), 2)} MB) exceeds 30 MB maximum limit.",
    }


def validate_saved_artifact(
    saved_path: Path,
    expected_format: str = "JPEG",
    min_short_side: int = MIN_SHORT,
    target_ratio: float = TARGET_RATIO,
    ratio_tolerance: float = RATIO_TOL,
    min_dpi: int = MIN_DPI,
    max_bytes: int = MAX_FILE_BYTES,
    upscaled: bool = False,
    upscale_info: dict | None = None,
) -> tuple[bool, dict[str, Any], list[CheckResult]]:
    """
    Physical Post-Export Artifact Re-Validation.
    Reopens the actual file saved on disk and verifies:
    1. File exists on disk
    2. File can be opened and decoded (not corrupted/truncated)
    3. Format == expected_format (JPEG)
    4. Color mode == RGB
    5. Dimensions and shortest side >= min_short_side (>= 2900 px)
    6. Aspect ratio matches 1.4:1 within tolerance
    7. Stored DPI >= min_dpi (>= 300)
    8. Actual byte size <= max_bytes (<= 30 MB)
    9. Embedded ICC profile status
    """
    checks: list[CheckResult] = []

    if not saved_path.is_file():
        checks.append(CheckResult(
            id="artifact_exists",
            name="Exported File Exists",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.FAIL,
            message=f"Exported artifact not found on disk: {saved_path}",
        ))
        return False, {}, checks

    actual_bytes = saved_path.stat().st_size
    actual_mb = round(actual_bytes / (1024 * 1024), 3)

    try:
        with Image.open(saved_path) as disk_img:
            disk_img.load()
            fmt = disk_img.format
            mode = disk_img.mode
            w, h = disk_img.size
            dpi = _dpi(disk_img)
            icc_raw = disk_img.info.get("icc_profile")
            profile_name = _profile_name(disk_img)
    except Exception as exc:
        checks.append(CheckResult(
            id="artifact_readable",
            name="Artifact Readability & Integrity",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.FAIL,
            message=f"Saved artifact is corrupted or cannot be read: {exc}",
        ))
        return False, {}, checks

    short_side = min(w, h)
    long_side = max(w, h)
    ratio = round(long_side / short_side, 4)

    # 1. Format Check (Official requirement: JPEG)
    fmt_pass = (fmt == expected_format)
    checks.append(CheckResult(
        id="artifact_format",
        name="Artifact Format",
        category=CheckCategory.OFFICIAL_REQUIREMENTS,
        status=CheckStatus.PASS if fmt_pass else CheckStatus.FAIL,
        value=fmt,
        threshold=expected_format,
        threshold_type=ThresholdType.OFFICIAL,
        message=f"Saved format: {fmt}." if fmt_pass else f"Expected {expected_format}, got {fmt}.",
    ))

    # 2. Mode Check (Displate requires RGB)
    mode_pass = (mode == "RGB")
    checks.append(CheckResult(
        id="artifact_color_mode",
        name="Artifact Color Mode",
        category=CheckCategory.OFFICIAL_REQUIREMENTS,
        status=CheckStatus.PASS if mode_pass else CheckStatus.FAIL,
        value=mode,
        threshold="RGB",
        threshold_type=ThresholdType.OFFICIAL,
        message=f"Color mode: {mode}." if mode_pass else f"Expected RGB mode, got {mode}.",
    ))

    # 3. Minimum Short Side (Official requirement: >= 2900 px)
    short_pass = (short_side >= min_short_side)
    checks.append(CheckResult(
        id="artifact_minimum_short_side",
        name="Artifact Shortest Side",
        category=CheckCategory.OFFICIAL_REQUIREMENTS,
        status=CheckStatus.PASS if short_pass else CheckStatus.FAIL,
        value=short_side,
        unit="px",
        threshold=min_short_side,
        threshold_type=ThresholdType.OFFICIAL,
        operator=">=",
        message=f"Shortest side: {short_side}px (≥{min_short_side}px)." if short_pass else f"Shortest side {short_side}px is below minimum {min_short_side}px.",
    ))

    # 4. Aspect Ratio (Official requirement: 1.4:1)
    ratio_diff = abs(ratio - target_ratio)
    ratio_pass = (ratio_diff <= ratio_tolerance)
    checks.append(CheckResult(
        id="artifact_aspect_ratio",
        name="Artifact Aspect Ratio",
        category=CheckCategory.OFFICIAL_REQUIREMENTS,
        status=CheckStatus.PASS if ratio_pass else CheckStatus.FAIL,
        value=ratio,
        unit="ratio",
        threshold=target_ratio,
        threshold_type=ThresholdType.OFFICIAL,
        operator="==",
        message=f"Aspect ratio: {ratio}:1 (target {target_ratio}:1)." if ratio_pass else f"Aspect ratio {ratio}:1 does not match required {target_ratio}:1.",
    ))

    # 5. Stored DPI (Official requirement: >= 300)
    dpi_pass = (is_dpi_valid(dpi[0]) and is_dpi_valid(dpi[1]))
    checks.append(CheckResult(
        id="artifact_minimum_dpi",
        name="Artifact Stored DPI",
        category=CheckCategory.OFFICIAL_REQUIREMENTS,
        status=CheckStatus.PASS if dpi_pass else CheckStatus.FAIL,
        value=list(dpi),
        unit="dpi",
        threshold=min_dpi,
        threshold_type=ThresholdType.OFFICIAL,
        operator=">=",
        message=f"Stored DPI: {dpi[0]}×{dpi[1]} (≥{min_dpi})." if dpi_pass else f"Stored DPI {dpi[0]}×{dpi[1]} is below minimum {min_dpi} DPI.",
    ))

    # 6. File Size (Official requirement: <= 30 MB)
    size_pass = (actual_bytes <= max_bytes)
    checks.append(CheckResult(
        id="artifact_maximum_file_size",
        name="Artifact File Size",
        category=CheckCategory.OFFICIAL_REQUIREMENTS,
        status=CheckStatus.PASS if size_pass else CheckStatus.FAIL,
        value=actual_mb,
        unit="MB",
        threshold=round(max_bytes / (1024 * 1024), 1),
        threshold_type=ThresholdType.OFFICIAL,
        operator="<=",
        message=f"Saved file size: {actual_mb} MB (≤{round(max_bytes / (1024 * 1024), 1)} MB)." if size_pass else f"File size {actual_mb} MB exceeds limit.",
    ))

    # 7. ICC Profile (Recommendation)
    has_icc = bool(icc_raw)
    checks.append(CheckResult(
        id="artifact_color_profile",
        name="Artifact Color Profile",
        category=CheckCategory.RECOMMENDATIONS,
        status=CheckStatus.PASS if has_icc else CheckStatus.WARNING,
        value=profile_name,
        threshold="sRGB",
        threshold_type=ThresholdType.RECOMMENDATION,
        message=f"Embedded profile: {profile_name}." if has_icc else "No embedded ICC profile.",
    ))

    is_valid = all(
        c.status != CheckStatus.FAIL
        for c in checks
        if c.category == CheckCategory.OFFICIAL_REQUIREMENTS
    )

    artifact_info = {
        "file_path": str(saved_path),
        "width": w,
        "height": h,
        "dpi": list(dpi),
        "color_profile": profile_name,
        "has_icc_profile": has_icc,
        "format": fmt,
        "mode": mode,
        "file_bytes": actual_bytes,
        "file_mb": actual_mb,
        "aspect_ratio": ratio,
        "safe_margin_px": SAFE_MARGIN,
        "upscaled": upscaled,
        "upscale_info": upscale_info,
        "verified_on_disk": True,
        "artifact_validation_status": "PASS" if is_valid else "FAIL",
    }

    return is_valid, artifact_info, checks


def make_safe_preview(image: Image.Image, output: Path):
    preview   = image.copy().convert("RGB")
    w, h      = preview.size
    draw      = ImageDraw.Draw(preview)
    margin    = SAFE_MARGIN
    thickness = max(6, int(min(w, h) * 0.0025))
    draw.rectangle(
        [margin, margin, w - margin - 1, h - margin - 1],
        outline=(255, 0, 0), width=thickness,
    )
    preview.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    preview.save(output, "JPEG", quality=90)


# ── Core: prepare ─────────────────────────────────────────────────────────────

def prepare(
    path: Path,
    output_dir: Path,
    focus: str = "center",
    resize_mode: str | ResizeMode = ResizeMode.RECOMMENDED_SIZE,
    target_dpi: int = 300,
    normalize_srgb: bool = True,
    auto_upscale: bool = True,
    upscale_model: str = "realesrgan-x4plus",
    texture_enabled: bool = False,
    texture_type: str = "fine_paper",
    texture_strength: str = "very_subtle",
) -> dict[str, Any]:
    """
    Prepares artwork for Displate upload adhering to Phase 3 requirements:
    - Normalizes EXIF orientation before dimension calculations
    - Optionally applies AI Super-Resolution (Real-ESRGAN) FIRST if shortest side < 2900 px
    - Crops to 1.4:1 ratio without stretching or distortion
    - Resizes to Displate's official optimal dimensions (4000x5600 portrait / 5600x4000 landscape)
    - Performs true ICC-aware color conversion to sRGB with embedded profile
    - Exports JPEG with deterministic file-size optimization (<= 30 MB, 300 DPI)
    - Reopens and physically re-validates the saved artifact from disk
    - Strictly non-destructive (source file is never overwritten)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    working_path = path
    upscale_record = None

    upscale_error = None
    # 0. AI Super-Resolution pre-pass: Upscale BEFORE any resize or crop
    try:
        with Image.open(path) as raw:
            chk_img = ImageOps.exif_transpose(raw)
            chk_w, chk_h = chk_img.size
        
        if auto_upscale and min(chk_w, chk_h) < MIN_SHORT:
            from upscaler import is_upscaler_available, calculate_required_scale, upscale_image
            if is_upscaler_available():
                scale = calculate_required_scale(chk_w, chk_h, target_short_side=RECOMMENDED_SHORT)
                upscaled_path = output_dir / f"upscaled_source_{scale}x.png"
                upscale_res = upscale_image(
                    input_path=path,
                    output_path=upscaled_path,
                    scale=scale,
                    model_name=upscale_model,
                )
                upscale_record = upscale_res
                working_path = upscaled_path
            else:
                upscale_error = "Real-ESRGAN upscaler engine not found on system."
    except Exception as exc:
        upscale_error = f"AI upscaling failed: {exc}"

    report = analyze(working_path, focus, upscale_applied=upscale_record)
    input_short = min(report["input"]["width"], report["input"]["height"])
    if input_short < MIN_SHORT:
        return {
            "prepared": False,
            "report": report,
            "error": upscale_error or f"Shortest side ({input_short}px) is below official 2900px minimum. AI upscaling is required.",
        }

    # Guarantee source file integrity: never overwrite the original artwork
    final_path = output_dir / "displate_ready.jpg"
    if final_path.resolve() == path.resolve():
        final_path = output_dir / f"displate_ready_prepared_{int(time.time())}.jpg"

    preview_path = output_dir / "safe_area_preview.jpg"
    if preview_path.resolve() == path.resolve():
        preview_path = output_dir / f"safe_area_preview_prepared_{int(time.time())}.jpg"

    with Image.open(working_path) as raw:
        # 1. EXIF orientation normalization BEFORE all dimension calculations
        image = ImageOps.exif_transpose(raw)

        # 2. Crop to target aspect ratio (1.4:1) without stretching
        crop_box = tuple(report["crop_box"])
        image = image.crop(crop_box)
        crop_w, crop_h = image.size

        # 3. ICC-aware color conversion
        image, color_info = _convert_to_srgb(image, normalize=normalize_srgb)
        icc_bytes = image.info.get("icc_profile") or _srgb_icc()

        # 4. Explicit resize mode: standardize to Displate optimal dimensions
        image, resize_info = _apply_resize_mode(image, resize_mode, was_upscaled=bool(upscale_record))
        final_w, final_h = image.size

        # 5. Check minimum short side (NO artificial upscaling allowed)
        if min(final_w, final_h) < MIN_SHORT:
            report["blocked"] = True
            report["ready_for_prepare"] = False
            if "overall" in report and isinstance(report["overall"], dict):
                report["overall"]["status"] = OverallStatus.BLOCKED.value
            report["final_status"] = OverallStatus.BLOCKED.value
            return {
                "prepared": False,
                "report": report,
                "error": f"Shortest side {min(final_w, final_h)}px is below 2900px minimum. Upscaling is not allowed.",
            }

        # 6. Optional Flat-Area Texture Enhancement & JPEG export
        texture_record: dict[str, Any] = {
            "applied": False,
            "enabled": texture_enabled,
            "texture_type": texture_type,
            "strength": texture_strength,
        }

        # Generate heatmap preview if flat regions exist
        mask_preview_path = output_dir / "flat_mask_preview.png"
        preview_dir_path = output_dir / "preview" / "flat_mask_preview.png"
        flat_ratio, full_mask, analysis_mask, flat_stats = detect_flat_areas(
            image,
            max_dim=PROFILE.flat_detection_max_px,
            variance_threshold=PROFILE.flat_variance_threshold,
            edge_density_threshold=PROFILE.flat_edge_density_threshold,
            min_region_pixels=PROFILE.flat_min_region_pixels,
            edge_protection_distance=PROFILE.flat_edge_protection_distance,
            target_size=image.size,
        )
        if np.any(full_mask > 0.05):
            generate_heatmap_preview(image, full_mask, output_path=mask_preview_path)
            generate_heatmap_preview(image, full_mask, output_path=preview_dir_path)

        if texture_enabled and np.any(full_mask > 0.05):
            fingerprint = compute_image_fingerprint(working_path)
            tex_seed = seed_from_fingerprint(fingerprint)

            fallback_order = ["medium", "subtle", "very_subtle", "off"]
            start_idx = fallback_order.index(texture_strength) if texture_strength in fallback_order else 2

            export_info = None
            for current_strength in fallback_order[start_idx:]:
                if current_strength == "off":
                    export_info = _save_jpeg_under_limit(
                        image=image,
                        output=final_path,
                        target_dpi=target_dpi,
                        icc_bytes=icc_bytes,
                        resize_mode=resize_mode,
                    )
                    texture_record = {
                        "applied": False,
                        "enabled": True,
                        "fallback": True,
                        "reason": "disabled_to_satisfy_30mb_limit",
                        "warning": "Texture enhancement disabled to satisfy 30 MB file size limit.",
                    }
                    break

                current_amp = PROFILE.texture_amplitudes.get(current_strength, 0.008)
                candidate_img, t_stats = apply_luminance_texture(
                    image=image,
                    mask=full_mask,
                    amplitude=current_amp,
                    seed=tex_seed,
                    texture_type=texture_type,
                )

                exp_res = _save_jpeg_under_limit(
                    image=candidate_img,
                    output=final_path,
                    target_dpi=target_dpi,
                    icc_bytes=icc_bytes,
                    resize_mode=resize_mode,
                )

                if exp_res.get("success"):
                    export_info = exp_res
                    image = candidate_img
                    texture_record = {
                        "applied": True,
                        "enabled": True,
                        "texture_type": texture_type,
                        "strength": current_strength,
                        "amplitude": current_amp,
                        "seed": tex_seed,
                        "fingerprint": fingerprint,
                        "reduced_strength": current_strength != texture_strength,
                        "stats": t_stats,
                    }
                    break

            if export_info is None or not export_info.get("success"):
                export_info = _save_jpeg_under_limit(
                    image=image,
                    output=final_path,
                    target_dpi=target_dpi,
                    icc_bytes=icc_bytes,
                    resize_mode=resize_mode,
                )
        else:
            export_info = _save_jpeg_under_limit(
                image=image,
                output=final_path,
                target_dpi=target_dpi,
                icc_bytes=icc_bytes,
                resize_mode=resize_mode,
            )

        if not export_info["success"]:
            report["blocked"] = True
            report["ready_for_prepare"] = False
            if "overall" in report and isinstance(report["overall"], dict):
                report["overall"]["status"] = OverallStatus.BLOCKED.value
            report["final_status"] = OverallStatus.BLOCKED.value
            return {
                "prepared": False,
                "report": report,
                "error": export_info.get("error", "Failed to compress under 30 MB limit."),
            }

        # 7. Safe area preview
        make_safe_preview(image, preview_path)

    # 8. Physical Post-Export Artifact Re-Validation
    is_valid, artifact_info, artifact_checks = validate_saved_artifact(
        saved_path=final_path,
        expected_format="JPEG",
        min_short_side=MIN_SHORT,
        target_ratio=TARGET_RATIO,
        ratio_tolerance=RATIO_TOL,
        min_dpi=MIN_DPI,
        max_bytes=MAX_FILE_BYTES,
        upscaled=bool(upscale_record),
        upscale_info=upscale_record,
    )

    if not is_valid:
        report["blocked"] = True
        report["ready_for_prepare"] = False
        if "overall" in report and isinstance(report["overall"], dict):
            report["overall"]["status"] = OverallStatus.BLOCKED.value
            report["overall"]["blocking_failures"] += 1
        report["final_status"] = OverallStatus.BLOCKED.value
        return {
            "prepared": False,
            "report": report,
            "final_path": str(final_path),
            "preview_path": str(preview_path),
            "error": "Saved artifact failed physical re-validation on disk.",
        }

    # 9. Build comprehensive final report
    processing_record = {
        "orientation_normalized": True,
        "upscale": upscale_record or {"performed": False},
        "crop": {
            "performed": report.get("crop_kind", "none") != "none",
            "mode": focus,
            "box": list(crop_box),
            "size": [crop_w, crop_h],
            "original_size": [report["input"]["width"], report["input"]["height"]],
            "original_ratio": round(max(report["input"]["width"], report["input"]["height"]) / min(report["input"]["width"], report["input"]["height"]), 4),
            "target_ratio": TARGET_RATIO,
            "crop_kind": report.get("crop_kind", "none"),
        },
        "resize": resize_info,
        "texture": texture_record,
        "color_conversion": color_info,
        "jpeg_export": export_info,
        "crop_box": list(crop_box),
        "crop_size": [crop_w, crop_h],
        "crop_kind": report.get("crop_kind", "none"),
        "focus": focus,
    }

    final_record = {
        **artifact_info,
        "quality": export_info["quality"],
        "artifact_checks": [c.to_dict() for c in artifact_checks],
    }

    val_complete = bool(report.get("overall", {}).get("validation_complete", False))
    ready_for_prep = (not report.get("blocked", False)) and val_complete

    final_report = {
        **report,
        "blocked": False,
        "ready_for_prepare": ready_for_prep,
        "processing": processing_record,
        "final": final_record,
        "manual_review": [
            "Check safe_area_preview.jpg — keep faces, text, and signatures inside the red rectangle.",
            "Inspect displate_ready.jpg at 100% zoom for blur, grain, artifacts, logos, or watermarks.",
            "Confirm you own or are licensed to use every element in the artwork.",
            "Technical preparation does not guarantee Displate marketplace approval.",
        ],
    }

    return {
        "prepared": True,
        "report": final_report,
        "final_path": str(final_path),
        "preview_path": str(preview_path),
    }


# ── Unified one-shot ───────────────────────────────────────────────────────────

def analyze_and_prepare(
    path: Path,
    output_dir: Path,
    focus: str = "center",
    resize_mode: str | ResizeMode = ResizeMode.RECOMMENDED_SIZE,
    target_dpi: int = 300,
    normalize_srgb: bool = True,
    auto_upscale: bool = True,
    upscale_model: str = "realesrgan-x4plus",
    texture_enabled: bool = False,
    texture_type: str = "fine_paper",
    texture_strength: str = "very_subtle",
) -> dict[str, Any]:
    """Runs analyze then prepare in one call. Returns steps list + full result."""
    steps: list[dict] = []

    def step(name: str, status: str, message: str):
        steps.append({"name": name, "status": status, "message": message,
                      "ts": round(time.time() * 1000)})

    step("open", "done", f"Opened {path.name}")

    # Check if upscaling will be needed
    will_upscale = False
    try:
        with Image.open(path) as raw:
            chk_img = ImageOps.exif_transpose(raw)
            chk_w, chk_h = chk_img.size
            if auto_upscale and min(chk_w, chk_h) < MIN_SHORT:
                will_upscale = True
    except Exception:
        pass

    result = prepare(
        path,
        output_dir,
        focus,
        resize_mode=resize_mode,
        target_dpi=target_dpi,
        normalize_srgb=normalize_srgb,
        auto_upscale=auto_upscale,
        upscale_model=upscale_model,
        texture_enabled=texture_enabled,
        texture_type=texture_type,
        texture_strength=texture_strength,
    )

    if will_upscale:
        rep = result.get("report", {})
        up_rec = (rep.get("processing", {}).get("upscale")
                  or rep.get("final", {}).get("upscale_info")
                  or rep.get("input", {}).get("upscale_info"))
        if up_rec and up_rec.get("success"):
            step("upscale", "done", f"AI Super-Resolution ({up_rec.get('scale', 4)}x via {up_rec.get('model', 'Real-ESRGAN')}) completed in {up_rec.get('elapsed_seconds', 0)}s.")
        elif not result.get("prepared"):
            step("upscale", "fail", "AI upscaling could not be completed.")

    if not result["prepared"]:
        err_msg = result.get("error", "Source blocked by validation checks.")
        step("blocked", "fail", err_msg)
        return {"steps": steps, **result}

    rep = result["report"]
    crop_info = rep.get("processing", {}).get("crop", {})
    crop_size = crop_info.get("size", rep.get("crop_size", []))
    step("analyze", "done", "Preflight checks completed.")
    if crop_info.get("performed", False):
        step("crop", "done", f"Cropped to 5:7 ratio ({crop_size[0]}×{crop_size[1]}px, {rep.get('focus', 'center')}).")
    else:
        step("crop", "done", f"No crop needed — aspect ratio is already 1.4:1 ({crop_size[0]}×{crop_size[1]}px).")

    resize_info = rep.get("processing", {}).get("resize", {})
    final_info = rep.get("final", {})
    if resize_info.get("performed"):
        step("resize", "done", f"Resized to Displate optimal standard ({final_info.get('width', 4000)}×{final_info.get('height', 5600)}px Lanczos).")
    else:
        step("resize", "done", f"Dimensions verified ({final_info.get('width', 4000)}×{final_info.get('height', 5600)}px).")

    color_info = rep.get("processing", {}).get("color_conversion", {})
    if color_info.get("performed"):
        step("color", "done", f"Color converted from {color_info.get('source_profile')} to sRGB.")
    else:
        step("color", "done", "sRGB color profile verified and embedded.")

    tex_rec = rep.get("processing", {}).get("texture", {})
    if tex_rec.get("applied"):
        step("texture", "done", f"Applied {tex_rec.get('texture_type', 'fine_paper')} micro-texture ({tex_rec.get('strength', 'very_subtle')}) to flat surfaces.")
    elif tex_rec.get("fallback"):
        step("texture", "warn", "Texture enhancement disabled to maintain <= 30 MB file size.")

    export_info = rep.get("processing", {}).get("jpeg_export", {})
    step("export", "done", f"JPEG exported — {final_info.get('file_mb', 0)} MB (quality {export_info.get('quality', 90)}).")
    step("preview", "done", f"Safe-area preview created ({SAFE_MARGIN}px reference margin).")
    step("verify", "done", f"Physical artifact verified on disk ({final_info.get('width')}×{final_info.get('height')}px, {final_info.get('dpi', [300])[0]} DPI).")
    step("complete", "done", "Artwork is ready for Displate.")

    return {"steps": steps, **result}
