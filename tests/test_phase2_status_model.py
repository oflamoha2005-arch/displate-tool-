"""
Phase 2 Unit Tests: Status Model, CheckResult Categorization,
Deterministic Aggregation, and DisplateReport Schema.
"""
from __future__ import annotations

import json
import unittest

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


class TestPhase2StatusModel(unittest.TestCase):
    """Test suite for Phase 2 status model and report schema."""

    def _make_base_official_checks(self, dpi: int = 300, short_side: int = 3000) -> list[CheckResult]:
        return [
            CheckResult(
                id="minimum_short_side",
                name="Minimum Short Side",
                category=CheckCategory.OFFICIAL_REQUIREMENTS,
                status=CheckStatus.PASS if short_side >= 2900 else CheckStatus.FAIL,
                value=short_side,
                threshold=2900,
                threshold_type=ThresholdType.OFFICIAL,
            ),
            CheckResult(
                id="target_aspect_ratio",
                name="Target Aspect Ratio",
                category=CheckCategory.OFFICIAL_REQUIREMENTS,
                status=CheckStatus.PASS,
                value=1.4,
                threshold=1.4,
                threshold_type=ThresholdType.OFFICIAL,
            ),
            CheckResult(
                id="minimum_dpi",
                name="Minimum 300 DPI",
                category=CheckCategory.OFFICIAL_REQUIREMENTS,
                status=CheckStatus.PASS if dpi >= 300 else CheckStatus.FAIL,
                value=[dpi, dpi],
                threshold=300,
                threshold_type=ThresholdType.OFFICIAL,
            ),
            CheckResult(
                id="maximum_file_size",
                name="Maximum File Size",
                category=CheckCategory.OFFICIAL_REQUIREMENTS,
                status=CheckStatus.PASS,
                value=20.0,
                threshold=30,
                threshold_type=ThresholdType.OFFICIAL,
            ),
        ]

    # TEST 1: PASS official requirement -> check status PASS
    def test_01_pass_official_requirement(self):
        c = CheckResult(
            id="minimum_short_side",
            name="Minimum Short Side",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.PASS,
            value=3200,
            threshold=2900,
            threshold_type=ThresholdType.OFFICIAL,
        )
        self.assertEqual(c.status, CheckStatus.PASS)
        self.assertEqual(c.category, CheckCategory.OFFICIAL_REQUIREMENTS)
        self.assertEqual(c.threshold_type, ThresholdType.OFFICIAL)

    # TEST 2: Official requirement failure -> check status FAIL
    def test_02_fail_official_requirement(self):
        c = CheckResult(
            id="minimum_short_side",
            name="Minimum Short Side",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.FAIL,
            value=2400,
            threshold=2900,
            threshold_type=ThresholdType.OFFICIAL,
        )
        self.assertEqual(c.status, CheckStatus.FAIL)
        self.assertEqual(c.category, CheckCategory.OFFICIAL_REQUIREMENTS)

    # TEST 3: Official FAIL -> overall BLOCKED
    def test_03_official_fail_produces_blocked(self):
        checks = self._make_base_official_checks(short_side=2800)
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.BLOCKED)
        self.assertEqual(counts["blocking_failures"], 1)

    # TEST 4: Internal heuristic WARNING -> overall READY_WITH_WARNINGS (not BLOCKED)
    def test_04_heuristic_warning_produces_ready_with_warnings(self):
        checks = self._make_base_official_checks()
        # Add a heuristic warning
        checks.append(
            CheckResult(
                id="brightness",
                name="Brightness Heuristic",
                category=CheckCategory.INTERNAL_HEURISTICS,
                status=CheckStatus.WARNING,
                value=32,
                threshold=40,
                threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertNotEqual(overall, OverallStatus.BLOCKED)
        self.assertEqual(counts["warnings"], 1)
        self.assertEqual(counts["blocking_failures"], 0)

    # TEST 5: Recommendation WARNING -> does not produce BLOCKED
    def test_05_recommendation_warning_not_blocked(self):
        checks = self._make_base_official_checks()
        checks.append(
            CheckResult(
                id="recommended_dimensions",
                name="Recommended Dimensions",
                category=CheckCategory.RECOMMENDATIONS,
                status=CheckStatus.WARNING,
                value="3000x4200",
                threshold="4000x5600",
                threshold_type=ThresholdType.RECOMMENDATION,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertNotEqual(overall, OverallStatus.BLOCKED)

    # TEST 6: Manual confirmation NOT_CHECKED -> does not become PASS automatically
    def test_06_manual_confirmation_not_checked_preserved(self):
        checks = self._make_base_official_checks()
        checks.append(
            CheckResult(
                id="ownership_confirmation",
                name="Ownership",
                category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
                status=CheckStatus.NOT_CHECKED,
                threshold_type=ThresholdType.NONE,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        # Must not become pure READY; manual confirmation is pending
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertEqual(checks[-1].status, CheckStatus.NOT_CHECKED)
        self.assertNotEqual(checks[-1].status, CheckStatus.PASS)

    # TEST 7: 600 DPI -> official DPI check PASS
    def test_07_600_dpi_passes(self):
        c = CheckResult(
            id="minimum_dpi",
            name="Minimum 300 DPI",
            category=CheckCategory.OFFICIAL_REQUIREMENTS,
            status=CheckStatus.PASS if 600 >= DEFAULT_PROFILE.minimum_dpi else CheckStatus.FAIL,
            value=[600, 600],
            threshold=300,
            threshold_type=ThresholdType.OFFICIAL,
            operator=">=",
        )
        self.assertEqual(c.status, CheckStatus.PASS)

    # TEST 8: 299 DPI -> official DPI check FAIL -> overall BLOCKED
    def test_08_299_dpi_fails_and_blocks(self):
        checks = self._make_base_official_checks(dpi=299)
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.BLOCKED)
        self.assertGreaterEqual(counts["blocking_failures"], 1)

    # TEST 9: 5000x7000 -> recommendation only -> not BLOCKED because it exceeds 4000x5600
    def test_09_5000x7000_not_blocked(self):
        checks = self._make_base_official_checks(short_side=5000)
        checks.append(
            CheckResult(
                id="recommended_dimensions",
                name="Recommended Dimensions",
                category=CheckCategory.RECOMMENDATIONS,
                status=CheckStatus.PASS,
                value="5000x7000",
                threshold="4000x5600",
                threshold_type=ThresholdType.RECOMMENDATION,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY)
        self.assertNotEqual(overall, OverallStatus.BLOCKED)

    # TEST 10: AdobeRGB -> not automatically treated as official failure
    def test_10_adobergb_not_official_failure(self):
        norm = "Adobe RGB (1998)".lower()
        is_recognized = "adobe" in norm or "srgb" in norm
        c = CheckResult(
            id="preferred_color_profile",
            name="Color Profile",
            category=CheckCategory.RECOMMENDATIONS,
            status=CheckStatus.PASS if is_recognized else CheckStatus.WARNING,
            value="Adobe RGB (1998)",
            threshold="sRGB",
            threshold_type=ThresholdType.RECOMMENDATION,
        )
        self.assertEqual(c.status, CheckStatus.PASS)
        self.assertNotEqual(c.status, CheckStatus.FAIL)
        self.assertEqual(c.category, CheckCategory.RECOMMENDATIONS)

    # TEST 11: Local pHash match -> INTERNAL_HEURISTIC WARNING -> not BLOCKED
    def test_11_phash_match_is_warning_not_blocked(self):
        checks = self._make_base_official_checks()
        checks.append(
            CheckResult(
                id="near_duplicate",
                name="Near-Duplicate Detection",
                category=CheckCategory.INTERNAL_HEURISTICS,
                status=CheckStatus.WARNING,
                value=[{"job_id": "abc123", "distance": 4, "similarity": 93.8}],
                threshold=10,
                threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertNotEqual(overall, OverallStatus.BLOCKED)

    # TEST 12: No risk_score in canonical report
    def test_12_no_risk_score_in_canonical_report(self):
        rep = DisplateReport(
            overall={"status": OverallStatus.READY.value, "blocking_failures": 0, "warnings": 0, "not_checked": 0, "validation_complete": True}
        )
        data = rep.to_dict()
        self.assertNotIn("risk_score", data)
        self.assertNotIn("base_risk", data)
        self.assertNotIn("risk_penalties", data)

    # TEST 13: No risk_probability in canonical report
    def test_13_no_risk_probability_in_canonical_report(self):
        rep = DisplateReport()
        data = rep.to_dict()
        self.assertNotIn("rejection_probability", data)
        self.assertNotIn("approval_probability", data)
        self.assertNotIn("risk_percent", data)

    # TEST 14: Report serialization/deserialization works
    def test_14_report_serialization_roundtrip(self):
        checks = self._make_base_official_checks()
        rep = DisplateReport(
            report_version="1.0",
            tool={"name": "Displate One-Click Preparer", "version": "1.0.0"},
            input={"width": 3000, "height": 4200, "file_mb": 15.2},
            checks={
                "official_requirements": [c.to_dict() for c in checks],
                "recommendations": [],
                "internal_heuristics": [],
                "manual_user_confirmations": [],
            },
            overall={"status": OverallStatus.READY.value, "blocking_failures": 0, "warnings": 0, "not_checked": 0, "validation_complete": True},
        )
        d = rep.to_dict()
        raw_json = json.dumps(d)
        loaded_dict = json.loads(raw_json)
        rep2 = DisplateReport.from_dict(loaded_dict)
        self.assertEqual(rep2.report_version, "1.0")
        self.assertEqual(rep2.input["width"], 3000)
        self.assertEqual(rep2.overall["status"], OverallStatus.READY.value)

    # TEST 15: NOT_CHECKED remains distinguishable from PASS
    def test_15_not_checked_distinguishable_from_pass(self):
        c_pass = CheckResult(
            id="test_pass",
            name="Test Pass",
            category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
            status=CheckStatus.PASS,
        )
        c_nc = CheckResult(
            id="test_nc",
            name="Test Not Checked",
            category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
            status=CheckStatus.NOT_CHECKED,
        )
        self.assertNotEqual(c_pass.status, c_nc.status)
        self.assertEqual(c_pass.status, CheckStatus.PASS)
        self.assertEqual(c_nc.status, CheckStatus.NOT_CHECKED)

    # TEST 16: Multiple warnings aggregate correctly
    def test_16_multiple_warnings_aggregate_correctly(self):
        checks = self._make_base_official_checks()
        checks.append(CheckResult(id="w1", name="W1", category=CheckCategory.INTERNAL_HEURISTICS, status=CheckStatus.WARNING))
        checks.append(CheckResult(id="w2", name="W2", category=CheckCategory.INTERNAL_HEURISTICS, status=CheckStatus.WARNING))
        checks.append(CheckResult(id="w3", name="W3", category=CheckCategory.RECOMMENDATIONS, status=CheckStatus.WARNING))

        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertEqual(counts["warnings"], 3)
        self.assertEqual(counts["blocking_failures"], 0)

    # TEST 17: Multiple official failures aggregate correctly
    def test_17_multiple_official_failures_aggregate_correctly(self):
        checks = [
            CheckResult(id="f1", name="F1", category=CheckCategory.OFFICIAL_REQUIREMENTS, status=CheckStatus.FAIL),
            CheckResult(id="f2", name="F2", category=CheckCategory.OFFICIAL_REQUIREMENTS, status=CheckStatus.FAIL),
            CheckResult(id="p1", name="P1", category=CheckCategory.OFFICIAL_REQUIREMENTS, status=CheckStatus.PASS),
        ]
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.BLOCKED)
        self.assertEqual(counts["blocking_failures"], 2)

    # TEST 18: Mixed official PASS + heuristic WARNING -> READY_WITH_WARNINGS
    def test_18_mixed_official_pass_heuristic_warning(self):
        checks = self._make_base_official_checks()
        checks.append(
            CheckResult(
                id="border_check",
                name="Border Check",
                category=CheckCategory.INTERNAL_HEURISTICS,
                status=CheckStatus.WARNING,
                threshold_type=ThresholdType.INTERNAL_HEURISTIC,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertNotEqual(overall, OverallStatus.BLOCKED)
        self.assertNotEqual(overall, OverallStatus.READY)

    # ── Phase 2.1 Specific Tests ──────────────────────────────────────────────

    # TEST 19: INCOMPLETE_VALIDATION is not an overall status (exactly 3 statuses)
    def test_19_incomplete_validation_not_an_overall_status(self):
        self.assertFalse(hasattr(OverallStatus, "INCOMPLETE_VALIDATION"))
        expected_statuses = {OverallStatus.READY, OverallStatus.READY_WITH_WARNINGS, OverallStatus.BLOCKED}
        self.assertEqual(set(OverallStatus), expected_statuses)
        self.assertEqual(len(OverallStatus), 3)

    # TEST 20: Manual NOT_CHECKED -> validation_complete false
    def test_20_manual_not_checked_validation_complete_false(self):
        checks = self._make_base_official_checks()
        checks.append(
            CheckResult(
                id="ownership_confirmation",
                name="Ownership",
                category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
                status=CheckStatus.NOT_CHECKED,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertFalse(counts["validation_complete"])
        self.assertEqual(counts["not_checked"], 1)

    # TEST 21: Manual NOT_CHECKED -> ready_for_prepare false
    def test_21_manual_not_checked_ready_for_prepare_false(self):
        checks = self._make_base_official_checks()
        checks.append(
            CheckResult(
                id="ownership_confirmation",
                name="Ownership",
                category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
                status=CheckStatus.NOT_CHECKED,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        rep = DisplateReport(
            checks={"official": [c.to_dict() for c in checks]},
            overall={"status": overall.value, **counts},
        )
        data = rep.to_dict()
        self.assertEqual(data["final_status"], "READY_WITH_WARNINGS")
        self.assertFalse(data["overall"]["validation_complete"])
        self.assertFalse(data["ready_for_prepare"])

    # TEST 22: Completed manual confirmations allow ready_for_prepare true
    def test_22_completed_manual_confirmations_allow_ready_for_prepare_true(self):
        checks = self._make_base_official_checks()
        checks.append(
            CheckResult(
                id="ownership_confirmation",
                name="Ownership",
                category=CheckCategory.MANUAL_USER_CONFIRMATIONS,
                status=CheckStatus.PASS,
            )
        )
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY)
        self.assertTrue(counts["validation_complete"])

        rep = DisplateReport(
            checks={"official": [c.to_dict() for c in checks]},
            overall={"status": overall.value, **counts},
        )
        data = rep.to_dict()
        self.assertEqual(data["final_status"], "READY")
        self.assertTrue(data["overall"]["validation_complete"])
        self.assertTrue(data["ready_for_prepare"])

    # TEST 23: Existing official FAIL still produces BLOCKED and ready_for_prepare false
    def test_23_existing_official_fail_still_produces_blocked(self):
        checks = self._make_base_official_checks(short_side=2000)
        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.BLOCKED)
        rep = DisplateReport(
            checks={"official": [c.to_dict() for c in checks]},
            overall={"status": overall.value, **counts},
        )
        data = rep.to_dict()
        self.assertEqual(data["final_status"], "BLOCKED")
        self.assertTrue(data["blocked"])
        self.assertFalse(data["ready_for_prepare"])

    # TEST 24: Existing heuristic WARNING still produces READY_WITH_WARNINGS
    def test_24_existing_heuristic_warning_still_produces_ready_with_warnings(self):
        checks = self._make_base_official_checks()
        # All manual checks confirmed
        checks.append(CheckResult(id="ownership", name="Ownership", category=CheckCategory.MANUAL_USER_CONFIRMATIONS, status=CheckStatus.PASS))
        # Add a heuristic warning
        checks.append(CheckResult(id="border", name="Border", category=CheckCategory.INTERNAL_HEURISTICS, status=CheckStatus.WARNING))

        overall, counts = aggregate_overall_status(checks)
        self.assertEqual(overall, OverallStatus.READY_WITH_WARNINGS)
        self.assertTrue(counts["validation_complete"])

        rep = DisplateReport(
            checks={"official": [c.to_dict() for c in checks]},
            overall={"status": overall.value, **counts},
        )
        data = rep.to_dict()
        self.assertEqual(data["final_status"], "READY_WITH_WARNINGS")
        self.assertFalse(data["blocked"])
        self.assertTrue(data["overall"]["validation_complete"])
        self.assertTrue(data["ready_for_prepare"])


if __name__ == "__main__":
    unittest.main()
