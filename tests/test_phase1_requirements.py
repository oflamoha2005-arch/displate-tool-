"""
Phase 1 Unit Tests: Centralized Requirements Profile and Constants.
Verifies displate_artist_2026 profile loading, classification semantics,
boundary definitions, and constant integration.
"""
from __future__ import annotations

import copy
import unittest

from requirements_config import (
    DEFAULT_PROFILE,
    ProfileValidationError,
    RequirementsProfile,
    load_profile,
    validate_profile_dict,
)
import processor
import hash_store


class TestPhase1RequirementsProfile(unittest.TestCase):
    """Test suite for Phase 1 requirements centralization."""

    def setUp(self):
        self.profile = load_profile("displate_artist_2026")

    # TEST 1: Load the displate_artist_2026 profile successfully
    def test_01_load_profile_success(self):
        self.assertIsNotNone(self.profile)
        self.assertEqual(self.profile.profile_id, "displate_artist_2026")
        self.assertIsInstance(self.profile, RequirementsProfile)

    # TEST 2: Minimum short side is 2900
    def test_02_minimum_short_side(self):
        self.assertEqual(self.profile.minimum_short_side_px, 2900)
        item = self.profile.get_item("requirements", "minimum_short_side_px")
        self.assertEqual(item.value, 2900)
        self.assertEqual(item.unit, "px")
        self.assertEqual(item.threshold_type, "OFFICIAL")

    # TEST 3: Minimum DPI is 300
    def test_03_minimum_dpi(self):
        self.assertEqual(self.profile.minimum_dpi, 300)
        item = self.profile.get_item("requirements", "minimum_dpi")
        self.assertEqual(item.value, 300)
        self.assertEqual(item.unit, "dpi")
        self.assertEqual(item.threshold_type, "OFFICIAL")
        self.assertEqual(item.comparison, ">=")

    # TEST 4: Maximum file size is 30MB
    def test_04_maximum_file_size(self):
        self.assertEqual(self.profile.maximum_file_size_mb, 30)
        item = self.profile.get_item("requirements", "maximum_file_size_mb")
        self.assertEqual(item.value, 30)
        self.assertEqual(item.unit, "MB")
        self.assertEqual(item.threshold_type, "OFFICIAL")

    # TEST 5: Target ratio is 1.4
    def test_05_target_aspect_ratio(self):
        self.assertAlmostEqual(self.profile.target_aspect_ratio, 1.4, places=4)
        item = self.profile.get_item("requirements", "target_aspect_ratio")
        self.assertEqual(item.value, 1.4)
        self.assertEqual(item.threshold_type, "OFFICIAL")

    # TEST 6: Recommended portrait dimensions are 4000x5600
    def test_06_recommended_portrait_dimensions(self):
        self.assertEqual(self.profile.portrait_width_px, 4000)
        self.assertEqual(self.profile.portrait_height_px, 5600)
        w_item = self.profile.get_item("recommendations", "portrait_width_px")
        h_item = self.profile.get_item("recommendations", "portrait_height_px")
        self.assertEqual(w_item.threshold_type, "RECOMMENDATION")
        self.assertEqual(h_item.threshold_type, "RECOMMENDATION")

    # TEST 7: Recommended landscape dimensions are 5600x4000
    def test_07_recommended_landscape_dimensions(self):
        self.assertEqual(self.profile.landscape_width_px, 5600)
        self.assertEqual(self.profile.landscape_height_px, 4000)
        w_item = self.profile.get_item("recommendations", "landscape_width_px")
        h_item = self.profile.get_item("recommendations", "landscape_height_px")
        self.assertEqual(w_item.threshold_type, "RECOMMENDATION")
        self.assertEqual(h_item.threshold_type, "RECOMMENDATION")

    # TEST 8: sRGB is classified as RECOMMENDATION
    def test_08_preferred_color_profile_recommendation(self):
        self.assertEqual(self.profile.preferred_output_color_profile, "sRGB")
        item = self.profile.get_item("recommendations", "preferred_output_color_profile")
        self.assertEqual(item.value, "sRGB")
        self.assertEqual(item.threshold_type, "RECOMMENDATION")

    # TEST 9: 200px is classified as OFFICIAL_REFERENCE and linked to the 2900px reference short side
    def test_09_reference_safe_margin(self):
        self.assertEqual(self.profile.reference_safe_margin_px, 200)
        self.assertEqual(self.profile.reference_short_side_px, 2900)
        item = self.profile.get_item("requirements", "reference_safe_margin_px")
        self.assertEqual(item.value, 200)
        self.assertEqual(item.threshold_type, "OFFICIAL_REFERENCE")
        self.assertEqual(item.extra.get("reference_short_side_px"), 2900)
        self.assertEqual(item.extra.get("reference_file_dimensions"), "4060x2900")

    # TEST 10: 0.004 is classified as INTERNAL_HEURISTIC
    def test_10_ratio_comparison_epsilon_heuristic(self):
        self.assertEqual(self.profile.aspect_ratio_comparison_epsilon, 0.004)
        item = self.profile.get_item("internal_settings", "aspect_ratio_comparison_epsilon")
        self.assertEqual(item.value, 0.004)
        self.assertEqual(item.threshold_type, "INTERNAL_HEURISTIC")
        # Ensure description explicitly clarifies it is not an official Displate tolerance
        self.assertIn("not an official displate tolerance", item.description.lower())

    # TEST 11: pHash threshold, if present in the profile, is INTERNAL_HEURISTIC
    def test_11_phash_threshold_classification(self):
        self.assertEqual(self.profile.phash_distance_threshold, 10)
        item = self.profile.get_item("heuristics", "phash_distance_threshold")
        self.assertEqual(item.value, 10)
        self.assertEqual(item.threshold_type, "INTERNAL_HEURISTIC")

    # TEST 12: Invalid/missing profile fields are detected
    def test_12_invalid_or_missing_profile_fields(self):
        raw = self.profile.get_raw_dict()

        # Missing profile_id
        bad1 = copy.deepcopy(raw)
        bad1["profile_id"] = ""
        with self.assertRaises(ProfileValidationError):
            validate_profile_dict(bad1)

        # Non-positive minimum_short_side_px
        bad2 = copy.deepcopy(raw)
        bad2["requirements"]["minimum_short_side_px"]["value"] = 0
        with self.assertRaises(ProfileValidationError):
            validate_profile_dict(bad2)

        # Wrong threshold_type for official requirement
        bad3 = copy.deepcopy(raw)
        bad3["requirements"]["minimum_short_side_px"]["threshold_type"] = "RECOMMENDATION"
        with self.assertRaises(ProfileValidationError):
            validate_profile_dict(bad3)

        # minimum_dpi configured as exact ("==") instead of minimum (">=")
        bad4 = copy.deepcopy(raw)
        bad4["requirements"]["minimum_dpi"]["comparison"] = "=="
        with self.assertRaises(ProfileValidationError):
            validate_profile_dict(bad4)

        # Missing recommendations section
        bad5 = copy.deepcopy(raw)
        del bad5["recommendations"]
        with self.assertRaises(ProfileValidationError):
            validate_profile_dict(bad5)

        # Negative ratio epsilon
        bad6 = copy.deepcopy(raw)
        bad6["internal_settings"]["aspect_ratio_comparison_epsilon"]["value"] = -0.01
        with self.assertRaises(ProfileValidationError):
            validate_profile_dict(bad6)

    # TEST 13: 600 DPI is valid by profile semantics (DPI >= 300)
    def test_13_dpi_semantics_allows_high_dpi(self):
        self.assertTrue(self.profile.is_dpi_valid(300))
        self.assertTrue(self.profile.is_dpi_valid(600))
        self.assertTrue(self.profile.is_dpi_valid(1200))
        self.assertFalse(self.profile.is_dpi_valid(299))
        self.assertFalse(self.profile.is_dpi_valid(72))

    # TEST 14: Processor and HashStore constants correctly inherit from profile
    def test_14_subsystem_constants_inherited_from_profile(self):
        self.assertEqual(processor.MIN_SHORT, 2900)
        self.assertEqual(processor.TARGET_RATIO, 1.4)
        self.assertEqual(processor.SAFE_MARGIN, 200)
        self.assertEqual(processor.MAX_MB, 30)
        self.assertEqual(processor.RECOMMENDED_SHORT, 4000)
        self.assertEqual(processor.RECOMMENDED_LONG, 5600)
        self.assertEqual(processor.RATIO_TOL, 0.004)
        self.assertEqual(hash_store.HASH_THRESHOLD, 10)


if __name__ == "__main__":
    unittest.main()
