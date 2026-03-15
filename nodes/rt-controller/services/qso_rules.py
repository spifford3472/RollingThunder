"""
RollingThunder QSO rule engine for v0.3700.

Purpose:
    Apply business rules to a normalized canonical QSO draft.

This module:
    - accepts a canonical QSO dict
    - applies v0.3700 rules
    - returns an updated canonical QSO dict
    - does not write files
    - does not access Redis
    - does not publish events
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping, Optional


CanonicalQSO = Dict[str, Any]


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "y", "yes", "on"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalized_compare_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _ensure_defaults_applied_dict(qso: CanonicalQSO) -> Dict[str, Any]:
    existing = qso.get("defaults_applied")
    if isinstance(existing, dict):
        result = dict(existing)
    else:
        result = {}

    result.setdefault("rst_sent", None)
    result.setdefault("rst_rcvd", None)
    result.setdefault("qso_complete", None)
    result.setdefault("rule_profile", "v0.3700")
    return result


def _match_probable_duplicate(
    candidate: Mapping[str, Any],
    prior: Mapping[str, Any],
) -> bool:
    return (
        _normalized_compare_value(candidate.get("call"))
        == _normalized_compare_value(prior.get("call"))
        and _normalized_compare_value(candidate.get("band"))
        == _normalized_compare_value(prior.get("band"))
        and _normalized_compare_value(candidate.get("mode"))
        == _normalized_compare_value(prior.get("mode"))
    )


def _find_duplicate_basis(
    qso: Mapping[str, Any],
    recent_qsos: Optional[Iterable[Mapping[str, Any]]],
) -> Optional[str]:
    if not recent_qsos:
        return None

    for prior in recent_qsos:
        if _match_probable_duplicate(qso, prior):
            call = str(qso.get("call") or "").strip()
            band = str(qso.get("band") or "").strip()
            mode = str(qso.get("mode") or "").strip()
            return f"recent same call/band/mode match: {call} {band} {mode}"

    return None


def apply_qso_rules(
    qso: Mapping[str, Any],
    recent_qsos: Optional[Iterable[Mapping[str, Any]]] = None,
) -> CanonicalQSO:
    """
    Apply v0.3700 RollingThunder QSO rules to a normalized canonical QSO draft.

    Rules:
        - recent motion => force rst_sent/rst_rcvd to 59/59
        - stationary >= 300 sec => preserve manual RST if present, otherwise default missing to 59
        - stationary < 300 sec and no recent motion => force 59/59
        - qso_complete => "Y"
        - duplicate suspicion => same call + band + mode among recent_qsos
        - defaults_applied => machine-readable markers describing what was enforced/defaulted
    """
    result: CanonicalQSO = deepcopy(dict(qso))
    defaults_applied = _ensure_defaults_applied_dict(result)

    mobile_state = result.get("mobile_state")
    mobile_state = mobile_state if isinstance(mobile_state, dict) else {}

    recent_motion = _as_bool(mobile_state.get("recent_motion"))
    motion_free_sec = _as_int(mobile_state.get("motion_free_sec"), default=0)

    if recent_motion:
        result["rst_sent"] = "59"
        result["rst_rcvd"] = "59"
        defaults_applied["rst_sent"] = "forced_recent_motion"
        defaults_applied["rst_rcvd"] = "forced_recent_motion"

    elif motion_free_sec >= 300:
        if _is_blank(result.get("rst_sent")):
            result["rst_sent"] = "59"
            defaults_applied["rst_sent"] = "defaulted_stationary_missing"
        else:
            defaults_applied["rst_sent"] = defaults_applied["rst_sent"] or "preserved_manual_stationary"

        if _is_blank(result.get("rst_rcvd")):
            result["rst_rcvd"] = "59"
            defaults_applied["rst_rcvd"] = "defaulted_stationary_missing"
        else:
            defaults_applied["rst_rcvd"] = defaults_applied["rst_rcvd"] or "preserved_manual_stationary"

    else:
        # v0.3700 choice:
        # Until stationary for 300 sec, treat the contact as not sufficiently settled
        # and force 59/59 for deterministic behavior.
        result["rst_sent"] = "59"
        result["rst_rcvd"] = "59"
        defaults_applied["rst_sent"] = "forced_not_settled"
        defaults_applied["rst_rcvd"] = "forced_not_settled"

    result["qso_complete"] = "Y"
    defaults_applied["qso_complete"] = "enforced_v0_3700"

    duplicate_basis = _find_duplicate_basis(result, recent_qsos)
    if duplicate_basis:
        result["duplicate_suspected"] = True
        result["duplicate_basis"] = duplicate_basis
    else:
        result["duplicate_suspected"] = False
        result["duplicate_basis"] = None

    result["defaults_applied"] = defaults_applied
    return result


if __name__ == "__main__":
    import json

    recent = [
        {
            "call": "K1ABC",
            "band": "20m",
            "mode": "SSB",
        }
    ]

    demo_cases = {
        "Case A - moving": {
            "call": "W8XYZ",
            "band": "20m",
            "mode": "SSB",
            "rst_sent": "",
            "rst_rcvd": "",
            "mobile_state": {
                "recent_motion": True,
                "motion_free_sec": 0,
            },
        },
        "Case B - stationary manual": {
            "call": "W8XYZ",
            "band": "40m",
            "mode": "SSB",
            "rst_sent": "57",
            "rst_rcvd": "44",
            "mobile_state": {
                "recent_motion": False,
                "motion_free_sec": 301,
            },
        },
        "Case C - stationary missing RST": {
            "call": "W8XYZ",
            "band": "15m",
            "mode": "SSB",
            "rst_sent": "",
            "rst_rcvd": "",
            "mobile_state": {
                "recent_motion": False,
                "motion_free_sec": 600,
            },
        },
        "Case D - duplicate suspicion": {
            "call": "K1ABC",
            "band": "20m",
            "mode": "SSB",
            "rst_sent": "",
            "rst_rcvd": "",
            "mobile_state": {
                "recent_motion": False,
                "motion_free_sec": 600,
            },
        },
    }

    for title, qso in demo_cases.items():
        print(f"\n=== {title} ===")
        ruled = apply_qso_rules(qso, recent_qsos=recent)
        print(json.dumps(ruled, indent=2, sort_keys=True))