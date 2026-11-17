"""Illustrative clinical-document QA checks. Not a diagnostic tool or EHR integration."""
import re
from typing import Any

REQUIRED = {"hpi": str, "assessment": str, "plan": str}


def evaluate_clinical_note(note: dict[str, Any]) -> dict[str, Any]:
    errors, warnings = [], []
    for field, typ in REQUIRED.items():
        if field not in note:
            errors.append(f"Missing required field: {field}")
        elif not isinstance(note[field], typ) or not note[field].strip():
            errors.append(f"Field must be a non-empty string: {field}")
    if errors:
        return {"status": "REJECTED", "errors": errors, "warnings": warnings, "validated_fields": []}

    text = " ".join(note[k] for k in REQUIRED).lower()
    if re.search(r"\b(definitely|certainly|confirmed)\b", text):
        warnings.append("Review certainty language against source documentation.")
    if len(note["hpi"]) < 20:
        warnings.append("HPI is unusually short; verify source completeness.")
    return {
        "status": "REVIEW_REQUIRED" if warnings else "STRUCTURALLY_VALID",
        "errors": errors,
        "warnings": warnings,
        "validated_fields": list(REQUIRED.keys()),
        "disclaimer": "Structural/heuristic QA only; clinician review remains required.",
    }
