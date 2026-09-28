import pytest
from jobagg.pipelines.public_text_regression import inspira_ltr_mark_only_change


def row(body, source="un_inspira", identity="284750"):
    return {"source_id": source, "external_id": identity, "description": body}


def test_exact_ltr_marker_only_change_keeps_original_bytes():
    old, new = row("Article \u200e2.2(a) applies."), row("Article 2.2(a) applies.")
    assert inspira_ltr_mark_only_change(old, new)["accepted"]
    assert "\u200e" in old["description"]


@pytest.mark.parametrize(
    "new",
    [
        row("Article applies."),
        row("Article 2.2(a) applies.", source="other"),
        row("Article 2.2(a) applies.", identity="999"),
        row("Article \u200f2.2(a) applies."),
    ],
)
def test_substantive_identity_and_other_unicode_changes_still_rejected(new):
    assert not inspira_ltr_mark_only_change(row("Article \u200e2.2(a) applies."), new)["accepted"]


def test_lrm_in_rtl_text_requires_review():
    assert not inspira_ltr_mark_only_change(row("\u200eمرحبا"), row("مرحبا"))["accepted"]
