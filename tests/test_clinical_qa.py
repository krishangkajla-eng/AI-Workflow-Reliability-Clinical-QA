from clinical_qa import evaluate_clinical_note


def test_missing_field_rejected():
    result = evaluate_clinical_note({"hpi": "A sufficiently detailed history."})
    assert result["status"] == "REJECTED"


def test_valid_note_structurally_valid():
    result = evaluate_clinical_note({
        "hpi": "Patient reports a two-week history of intermittent knee pain.",
        "assessment": "Left knee pain.",
        "plan": "Obtain imaging and follow up.",
    })
    assert result["status"] == "STRUCTURALLY_VALID"


def test_certainty_language_flagged():
    result = evaluate_clinical_note({
        "hpi": "Patient reports a two-week history of intermittent knee pain.",
        "assessment": "Confirmed left knee pain.",
        "plan": "Obtain imaging and follow up.",
    })
    assert result["status"] == "REVIEW_REQUIRED"
