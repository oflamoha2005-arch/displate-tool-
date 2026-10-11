"""
Status Model and Report Schema for Displate Preparation.
Provides explicit enums, CheckResult models, deterministic status aggregation,
and the canonical DisplateReport schema.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class CheckStatus(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    NOT_CHECKED = "NOT_CHECKED"


class CheckCategory(str, enum.Enum):
    OFFICIAL_REQUIREMENTS = "OFFICIAL_REQUIREMENTS"
    RECOMMENDATIONS = "RECOMMENDATIONS"
    INTERNAL_HEURISTICS = "INTERNAL_HEURISTICS"
    MANUAL_USER_CONFIRMATIONS = "MANUAL_USER_CONFIRMATIONS"


class ThresholdType(str, enum.Enum):
    OFFICIAL = "OFFICIAL"
    OFFICIAL_REFERENCE = "OFFICIAL_REFERENCE"
    RECOMMENDATION = "RECOMMENDATION"
    INTERNAL_HEURISTIC = "INTERNAL_HEURISTIC"
    NONE = "NONE"


class OverallStatus(str, enum.Enum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    BLOCKED = "BLOCKED"


@dataclass
class CheckResult:
    """Canonical representation of a single check result."""
    id: str
    name: str
    category: CheckCategory
    status: CheckStatus
    value: Any = None
    unit: str | None = None
    threshold: Any = None
    threshold_type: ThresholdType = ThresholdType.NONE
    operator: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category.value,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "threshold": self.threshold,
            "threshold_type": self.threshold_type.value,
            "operator": self.operator,
            "message": self.message,
            "details": self.details,
            # Legacy UI compatibility fields:
            "code": self.id,
            "label": self.name,
            "status_lower": self.status.value.lower(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckResult:
        return cls(
            id=data["id"],
            name=data["name"],
            category=CheckCategory(data["category"]),
            status=CheckStatus(data["status"]),
            value=data.get("value"),
            unit=data.get("unit"),
            threshold=data.get("threshold"),
            threshold_type=ThresholdType(data.get("threshold_type", "NONE")),
            operator=data.get("operator"),
            message=data.get("message", ""),
            details=data.get("details", {}) or {},
        )


def aggregate_overall_status(checks: list[CheckResult]) -> tuple[OverallStatus, dict[str, Any]]:
    """
    Deterministically computes the overall status from check results.

    Rules:
    - Exactly three final statuses: BLOCKED, READY_WITH_WARNINGS, READY.
    - BLOCKED: If at least one OFFICIAL_REQUIREMENT has status == FAIL.
      (A heuristic WARNING or recommendation failure CANNOT create BLOCKED.)
    - READY_WITH_WARNINGS: If no official check has FAIL, but at least one check is WARNING
      or a check / manual confirmation is NOT_CHECKED.
    - READY: All applicable official requirements PASS, no warnings remain, and all manual checks are satisfied.
    - validation_complete: True if and only if not_checked == 0.
    """
    blocking_failures = 0
    warnings = 0
    not_checked = 0

    for c in checks:
        if c.status == CheckStatus.FAIL:
            if c.category == CheckCategory.OFFICIAL_REQUIREMENTS:
                blocking_failures += 1
            else:
                # Non-official failure is treated as warning
                warnings += 1
        elif c.status == CheckStatus.WARNING:
            warnings += 1
        elif c.status == CheckStatus.NOT_CHECKED:
            not_checked += 1

    validation_complete = (not_checked == 0)

    counts: dict[str, Any] = {
        "blocking_failures": blocking_failures,
        "warnings": warnings,
        "not_checked": not_checked,
        "validation_complete": validation_complete,
    }

    if blocking_failures > 0:
        return OverallStatus.BLOCKED, counts

    if warnings > 0 or not_checked > 0:
        return OverallStatus.READY_WITH_WARNINGS, counts

    return OverallStatus.READY, counts


@dataclass
class DisplateReport:
    """Canonical structured report model."""
    report_version: str = "1.0"
    tool: dict[str, Any] = field(default_factory=lambda: {
        "name": "Displate One-Click Preparer",
        "version": "1.0.0",
        "requirements_profile": "displate_artist_2026",
    })
    input: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {
        "official_requirements": [],
        "recommendations": [],
        "internal_heuristics": [],
        "manual_user_confirmations": [],
    })
    processing: dict[str, Any] = field(default_factory=dict)
    overall: dict[str, Any] = field(default_factory=dict)
    final: dict[str, Any] = field(default_factory=dict)
    manual_review: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serializes the report to a dictionary, including backward-compatible aliases."""
        flat_checks = []
        for cat_list in self.checks.values():
            flat_checks.extend(cat_list)

        overall_status_val = self.overall.get("status", OverallStatus.READY.value)
        is_blocked = (overall_status_val == OverallStatus.BLOCKED.value)
        val_complete = bool(self.overall.get("validation_complete", False))
        ready_for_prepare = (not is_blocked and val_complete)

        # Build clean canonical dictionary without fake risk scores
        data = {
            "report_version": self.report_version,
            "tool": self.tool,
            "input": self.input,
            "checks": self.checks,
            "checks_flat": flat_checks,
            "processing": self.processing,
            "overall": self.overall,
            "final_status": overall_status_val,
            "final": self.final,
            "manual_review": self.manual_review,
            # Backward compatibility fields for legacy frontend callers:
            "blocked": is_blocked,
            "ready_for_prepare": ready_for_prepare,
            "width": self.input.get("width"),
            "height": self.input.get("height"),
            "file_mb": self.input.get("file_mb"),
            "dpi": self.input.get("dpi"),
            "profile": self.input.get("color_profile"),
            "size_badge": self.input.get("size_badge", "minimum"),
            "crop_box": self.processing.get("crop", {}).get("box") if isinstance(self.processing.get("crop"), dict) else self.processing.get("crop_box", []),
            "crop_size": self.processing.get("crop", {}).get("size") if isinstance(self.processing.get("crop"), dict) else self.processing.get("crop_size", []),
            "crop_kind": self.processing.get("crop", {}).get("crop_kind") if isinstance(self.processing.get("crop"), dict) else self.processing.get("crop_kind", "none"),
            "focus": self.processing.get("focus") or (self.processing.get("crop", {}).get("mode", "center") if isinstance(self.processing.get("crop"), dict) else "center"),
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DisplateReport:
        return cls(
            report_version=data.get("report_version", "1.0"),
            tool=data.get("tool", {}),
            input=data.get("input", {}),
            checks=data.get("checks", {}),
            processing=data.get("processing", {}),
            overall=data.get("overall", {}),
            final=data.get("final", {}),
            manual_review=data.get("manual_review", []),
        )
