"""
Centralized requirements and configuration loader for Displate preparation.
Loads, validates, and exposes versioned requirements profiles.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PROFILES_DIR = BASE_DIR / "requirements_profiles"


class ProfileValidationError(ValueError):
    """Raised when a requirements profile fails validation checks."""
    pass


@dataclass(frozen=True)
class RequirementItem:
    value: Any
    threshold_type: str
    unit: str | None = None
    comparison: str | None = None
    description: str | None = None
    extra: dict[str, Any] | None = None


class RequirementsProfile:
    """Structured, typed access to a validated requirements profile."""

    def __init__(self, raw_data: dict[str, Any]):
        validate_profile_dict(raw_data)
        self._raw = raw_data

        self.profile_id: str = raw_data["profile_id"]
        self.name: str = raw_data.get("name", self.profile_id)
        self.version: str = raw_data.get("version", "1.0.0")

        # Official requirements
        reqs = raw_data["requirements"]
        self.minimum_short_side_px: int = int(reqs["minimum_short_side_px"]["value"])
        self.target_aspect_ratio: float = float(reqs["target_aspect_ratio"]["value"])
        self.minimum_dpi: int = int(reqs["minimum_dpi"]["value"])
        self.maximum_file_size_mb: int = int(reqs["maximum_file_size_mb"]["value"])
        self.maximum_file_size_bytes: int = self.maximum_file_size_mb * 1024 * 1024
        self.reference_safe_margin_px: int = int(reqs["reference_safe_margin_px"]["value"])
        self.reference_short_side_px: int = int(
            reqs["reference_safe_margin_px"].get("reference_short_side_px", 2900)
        )

        # Recommendations
        recs = raw_data["recommendations"]
        self.portrait_width_px: int = int(recs["portrait_width_px"]["value"])
        self.portrait_height_px: int = int(recs["portrait_height_px"]["value"])
        self.landscape_width_px: int = int(recs["landscape_width_px"]["value"])
        self.landscape_height_px: int = int(recs["landscape_height_px"]["value"])
        self.preferred_output_color_profile: str = str(
            recs["preferred_output_color_profile"]["value"]
        )
        self.preferred_output_format: str = str(
            recs["preferred_output_format"]["value"]
        )

        # Internal settings
        settings = raw_data.get("internal_settings", {})
        self.aspect_ratio_comparison_epsilon: float = float(
            settings["aspect_ratio_comparison_epsilon"]["value"]
        )

        # Heuristics
        heuristics = raw_data.get("heuristics", {})
        self.brightness_dark_threshold: int = int(
            heuristics.get("brightness_dark_threshold", {}).get("value", 40)
        )
        self.border_sample_px: int = int(
            heuristics.get("border_sample_px", {}).get("value", 25)
        )
        self.border_difference_threshold: float = float(
            heuristics.get("border_difference_threshold", {}).get("value", 28)
        )
        self.noise_flat_threshold: float = float(
            heuristics.get("noise_flat_threshold", {}).get("value", 8)
        )
        self.noise_warn_std: float = float(
            heuristics.get("noise_warn_std", {}).get("value", 22)
        )
        self.upscale_var_threshold: float = float(
            heuristics.get("upscale_var_threshold", {}).get("value", 60)
        )
        self.upscale_edge_threshold: float = float(
            heuristics.get("upscale_edge_threshold", {}).get("value", 10)
        )
        self.sharp_ratio_threshold: float = float(
            heuristics.get("sharp_ratio_threshold", {}).get("value", 2.0)
        )
        self.sharp_abs_threshold: float = float(
            heuristics.get("sharp_abs_threshold", {}).get("value", 30)
        )
        self.edge_content_abs: float = float(
            heuristics.get("edge_content_abs", {}).get("value", 15)
        )
        self.edge_content_ratio: float = float(
            heuristics.get("edge_content_ratio", {}).get("value", 1.4)
        )
        self.phash_distance_threshold: int = int(
            heuristics.get("phash_distance_threshold", {}).get("value", 10)
        )
        self.flat_area_ratio_threshold: float = float(
            heuristics.get("flat_area_ratio_threshold", {}).get("value", 0.15)
        )
        self.flat_variance_threshold: float = float(
            heuristics.get("flat_variance_threshold", {}).get("value", 12.0)
        )
        self.flat_edge_density_threshold: float = float(
            heuristics.get("flat_edge_density_threshold", {}).get("value", 0.08)
        )
        self.flat_min_region_pixels: int = int(
            heuristics.get("flat_min_region_pixels", {}).get("value", 2500)
        )
        self.flat_edge_protection_distance: int = int(
            heuristics.get("flat_edge_protection_distance", {}).get("value", 18)
        )
        self.flat_detection_max_px: int = int(
            heuristics.get("flat_detection_max_px", {}).get("value", 1500)
        )
        self.texture_enabled_default: bool = bool(
            heuristics.get("texture_enabled_default", {}).get("value", False)
        )
        self.texture_preset_default: str = str(
            heuristics.get("texture_preset_default", {}).get("value", "very_subtle")
        )
        self.texture_type_default: str = str(
            heuristics.get("texture_type_default", {}).get("value", "fine_paper")
        )
        self.texture_amplitudes: dict[str, float] = dict(
            heuristics.get("texture_amplitudes", {}).get("value", {
                "very_subtle": 0.008,
                "subtle": 0.015,
                "medium": 0.030,
            })
        )

    def get_raw_dict(self) -> dict[str, Any]:
        return self._raw

    def get_item(self, category: str, key: str) -> RequirementItem:
        cat_data = self._raw.get(category, {})
        if key not in cat_data:
            raise KeyError(f"Requirement item '{key}' not found in category '{category}'")
        item = cat_data[key]
        return RequirementItem(
            value=item.get("value"),
            threshold_type=item.get("threshold_type", "NONE"),
            unit=item.get("unit"),
            comparison=item.get("comparison"),
            description=item.get("description"),
            extra={k: v for k, v in item.items() if k not in {"value", "threshold_type", "unit", "comparison", "description"}}
        )

    def is_dpi_valid(self, dpi_val: float) -> bool:
        """Semantics: DPI must be >= minimum_dpi (300). Values above 300 are valid."""
        return dpi_val >= self.minimum_dpi


def validate_profile_dict(data: dict[str, Any]) -> None:
    """Validates structural and semantic constraints of a requirements profile."""
    if not isinstance(data, dict):
        raise ProfileValidationError("Profile data must be a JSON dictionary.")

    profile_id = data.get("profile_id")
    if not profile_id or not isinstance(profile_id, str):
        raise ProfileValidationError("Missing or invalid 'profile_id'.")

    reqs = data.get("requirements")
    if not isinstance(reqs, dict):
        raise ProfileValidationError("Missing 'requirements' section in profile.")

    # 1. minimum_short_side_px
    min_short = reqs.get("minimum_short_side_px")
    if not isinstance(min_short, dict) or min_short.get("value", 0) <= 0:
        raise ProfileValidationError("minimum_short_side_px must be a positive integer.")
    if min_short.get("threshold_type") != "OFFICIAL":
        raise ProfileValidationError("minimum_short_side_px threshold_type must be 'OFFICIAL'.")

    # 2. target_aspect_ratio
    ratio = reqs.get("target_aspect_ratio")
    if not isinstance(ratio, dict) or ratio.get("value", 0) <= 0:
        raise ProfileValidationError("target_aspect_ratio must be positive.")
    if ratio.get("threshold_type") != "OFFICIAL":
        raise ProfileValidationError("target_aspect_ratio threshold_type must be 'OFFICIAL'.")

    # 3. minimum_dpi
    min_dpi = reqs.get("minimum_dpi")
    if not isinstance(min_dpi, dict) or min_dpi.get("value", 0) <= 0:
        raise ProfileValidationError("minimum_dpi must be positive.")
    if min_dpi.get("threshold_type") != "OFFICIAL":
        raise ProfileValidationError("minimum_dpi threshold_type must be 'OFFICIAL'.")
    if min_dpi.get("comparison") != ">=":
        raise ProfileValidationError("minimum_dpi must be represented as a minimum (comparison '>=') rather than an exact target.")

    # 4. maximum_file_size_mb
    max_mb = reqs.get("maximum_file_size_mb")
    if not isinstance(max_mb, dict) or max_mb.get("value", 0) <= 0:
        raise ProfileValidationError("maximum_file_size_mb must be positive.")
    if max_mb.get("threshold_type") != "OFFICIAL":
        raise ProfileValidationError("maximum_file_size_mb threshold_type must be 'OFFICIAL'.")

    # 5. reference_safe_margin_px
    margin = reqs.get("reference_safe_margin_px")
    if not isinstance(margin, dict) or margin.get("value", 0) <= 0:
        raise ProfileValidationError("reference_safe_margin_px must be positive.")
    if margin.get("threshold_type") != "OFFICIAL_REFERENCE":
        raise ProfileValidationError("reference_safe_margin_px threshold_type must be 'OFFICIAL_REFERENCE'.")
    if margin.get("reference_short_side_px", 0) <= 0:
        raise ProfileValidationError("reference_short_side_px must be positive and linked to reference margin.")

    # 6. Recommendations
    recs = data.get("recommendations")
    if not isinstance(recs, dict):
        raise ProfileValidationError("Missing 'recommendations' section in profile.")

    for dim_key in ["portrait_width_px", "portrait_height_px", "landscape_width_px", "landscape_height_px"]:
        dim_item = recs.get(dim_key)
        if not isinstance(dim_item, dict) or dim_item.get("value", 0) <= 0:
            raise ProfileValidationError(f"{dim_key} must be a positive integer.")
        if dim_item.get("threshold_type") != "RECOMMENDATION":
            raise ProfileValidationError(f"{dim_key} threshold_type must be 'RECOMMENDATION'.")

    srgb_item = recs.get("preferred_output_color_profile")
    if not isinstance(srgb_item, dict) or srgb_item.get("threshold_type") != "RECOMMENDATION":
        raise ProfileValidationError("preferred_output_color_profile must be classified as 'RECOMMENDATION'.")

    format_item = recs.get("preferred_output_format")
    if not isinstance(format_item, dict) or format_item.get("threshold_type") != "RECOMMENDATION":
        raise ProfileValidationError("preferred_output_format must be classified as 'RECOMMENDATION'.")

    # 7. Internal settings
    settings = data.get("internal_settings")
    if not isinstance(settings, dict):
        raise ProfileValidationError("Missing 'internal_settings' section in profile.")
    eps_item = settings.get("aspect_ratio_comparison_epsilon")
    if not isinstance(eps_item, dict) or eps_item.get("value", -1) < 0:
        raise ProfileValidationError("aspect_ratio_comparison_epsilon must be non-negative.")
    if eps_item.get("threshold_type") != "INTERNAL_HEURISTIC":
        raise ProfileValidationError("aspect_ratio_comparison_epsilon threshold_type must be 'INTERNAL_HEURISTIC'.")

    # 8. Heuristics (if present)
    heuristics = data.get("heuristics", {})
    if isinstance(heuristics, dict):
        phash_item = heuristics.get("phash_distance_threshold")
        if phash_item and phash_item.get("threshold_type") != "INTERNAL_HEURISTIC":
            raise ProfileValidationError("phash_distance_threshold must be classified as 'INTERNAL_HEURISTIC'.")


_PROFILE_CACHE: dict[str, RequirementsProfile] = {}


def load_profile(profile_path_or_id: str | Path = "displate_artist_2026") -> RequirementsProfile:
    """
    Loads and caches a RequirementsProfile by ID or filesystem path.
    """
    cache_key = str(profile_path_or_id)
    if cache_key in _PROFILE_CACHE:
        return _PROFILE_CACHE[cache_key]

    target_path = Path(profile_path_or_id)
    if not target_path.is_file():
        # Check in PROFILES_DIR
        candidate = PROFILES_DIR / f"{profile_path_or_id}.json"
        if candidate.is_file():
            target_path = candidate
        else:
            raise FileNotFoundError(
                f"Requirements profile not found: '{profile_path_or_id}'. "
                f"Checked '{target_path}' and '{candidate}'."
            )

    try:
        content = target_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        raise ProfileValidationError(f"Could not read profile JSON from {target_path}: {exc}") from exc

    profile = RequirementsProfile(data)
    _PROFILE_CACHE[cache_key] = profile
    _PROFILE_CACHE[profile.profile_id] = profile
    return profile


# Default singleton instance for easy import across modules
DEFAULT_PROFILE = load_profile("displate_artist_2026")
