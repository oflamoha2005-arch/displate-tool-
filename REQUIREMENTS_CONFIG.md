# Displate Requirements & Configuration Profile (`displate_artist_2026`)

This document defines the requirements profile architecture for the Displate One-Click Preparer tool.
It categorizes all configuration values into four distinct semantic tiers.

---

## 1. Official Displate Requirements (`OFFICIAL`)

These represent verified, published technical mandates from the official Displate Shop Handbook and Artist upload documentation:

| Requirement | Value | Threshold Type | Semantic Rule | Description |
|---|---|---|---|---|
| **Minimum Short Side** | `2900 px` | `OFFICIAL` | `>= 2900 px` | Shortest side of the artwork canvas must be at least 2900 px. Files below this cannot be uploaded. |
| **Target Aspect Ratio** | `1.4 : 1` | `OFFICIAL` | `1.4:1` (5:7) | Required product ratio for Displate standard metal canvases (29 × 41.5 cm). |
| **Minimum Print Resolution** | `300 DPI` | `OFFICIAL` | `>= 300 DPI` | DPI metadata must be at least 300. Higher values (e.g. 600 DPI) are completely valid and accepted. |
| **Maximum File Size** | `30 MB` | `OFFICIAL` | `<= 30 MB` | Final uploaded image file size must not exceed 30 MB. |

---

## 2. Official Displate Reference Baseline (`OFFICIAL_REFERENCE`)

| Reference | Value | Threshold Type | Associated Baseline | Description |
|---|---|---|---|---|
| **Reference Safe Margin** | `200 px` | `OFFICIAL_REFERENCE` | `4060 × 2900 px` | Official guidance indicates keeping critical content (faces, text, signatures) at least 200 px from edges for the minimum-size 4060 × 2900 file. **This is not an official universal scaling formula for larger files.** |

---

## 3. Displate Recommendations (`RECOMMENDATION`)

These are official recommendations or tool normalization targets, NOT hard rejection boundaries:

| Item | Value | Threshold Type | Description |
|---|---|---|---|
| **Recommended Portrait Dimensions** | `4000 × 5600 px` | `RECOMMENDATION` | Recommended dimensions for optimal print quality. Larger dimensions (e.g., 5000 × 7000 px) are fully valid and must not be automatically rejected or forced downscaled. |
| **Recommended Landscape Dimensions** | `5600 × 4000 px` | `RECOMMENDATION` | Recommended dimensions for landscape orientation. |
| **Preferred Output Color Profile** | `sRGB` | `RECOMMENDATION` | Preferred color space for standardized output normalization. Displate accepts RGB workflows with embedded profiles (both sRGB and AdobeRGB 1998 are recognized). |
| **Preferred Output Format** | `JPEG` | `RECOMMENDATION` | Preferred export format produced by this preparation tool. |

---

## 4. Internal Computational & Quality Heuristics (`INTERNAL_HEURISTIC`)

> [!IMPORTANT]
> **Internal heuristic values are not official Displate thresholds.**
> They exist solely as preventive screening indicators within this local tool and do not reflect an official rejection standard from Displate.

| Parameter | Value | Threshold Type | Description |
|---|---|---|---|
| `aspect_ratio_comparison_epsilon` | `0.004` | `INTERNAL_HEURISTIC` | Floating-point numerical comparison epsilon; NOT an official Displate tolerance. |
| `phash_distance_threshold` | `10` | `INTERNAL_HEURISTIC` | Hamming distance threshold for local perceptual hash similarity; NOT an official Displate duplicate threshold. |
| `brightness_dark_threshold` | `40 / 255` | `INTERNAL_HEURISTIC` | Mean luminance advisory for very dark compositions. |
| `border_sample_px` / `border_diff` | `25 px` / `28` | `INTERNAL_HEURISTIC` | Edge band sampling for detecting possible artificial borders/frames. |
| `noise_flat_threshold` / `warn_std` | `8` / `22` | `INTERNAL_HEURISTIC` | Local patch standard deviation for grain/noise heuristic. |
| `upscale_var_threshold` / `edge` | `60` / `10` | `INTERNAL_HEURISTIC` | Local variance and edge density for potential artificial upscaling screening. |
| `sharp_ratio_threshold` / `abs` | `2.0` / `30` | `INTERNAL_HEURISTIC` | Edge RMS ratio for over-sharpening halo detection. |
| `edge_content_abs` / `ratio` | `15` / `1.4` | `INTERNAL_HEURISTIC` | Edge density ratio in border strip for safe-margin advisory. |

---

## 5. System Limits (Distinct from Displate Requirements)

The application also enforces server/workstation limits that are **not Displate Artist requirements**:
* `MAX_UPLOAD_MB = 80`: Maximum upload size accepted by the local web server.
* `MAX_BATCH = 5`: Maximum concurrent images in a batch job.
