import pytest

from app.domain import normalize_content, request_fingerprint


def test_hangul_nfc_is_same_submission_intent():
    composed = normalize_content("  기뻐  ")
    decomposed = normalize_content("기뻐")
    assert composed == decomposed == "기뻐"
    assert request_fingerprint(composed, "heart") == request_fingerprint(decomposed, "heart")


def test_family_emoji_counts_as_one_visible_character():
    family = "👨‍👩‍👧‍👦"
    assert normalize_content(family * 30) == family * 30
    with pytest.raises(ValueError):
        normalize_content(family * 31)


@pytest.mark.parametrize("value", ["   ", "가" * 31, "안녕\n친구", "안녕\u202e친구", "a" + "\u0301" * 1100])
def test_text_boundaries_reject_without_silently_deleting(value):
    with pytest.raises(ValueError):
        normalize_content(value)
