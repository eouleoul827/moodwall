import hashlib
import json

import pytest

from app.profanity import ProfanityFilter, jamo_view


@pytest.mark.parametrize("text", ["씨발", "씨%발", "씨ㅣ바ㄹ", "씨 %발", "씨..발", "시발"])
def test_curated_variants_reject(text):
    result = ProfanityFilter().scan(text)
    assert result.decision == "CONFIRMED"
    assert len(result.matches) == 1
    assert result.matches[0].rule_id.startswith("ko-explicit-")


@pytest.mark.parametrize(
    "text",
    [
        "시발점",
        "시 발점 찾았어",
        "씨앗 발아했어",
        "오늘 좀 화나네",
        "합격 축하해!",
        "씨a발",
        "씨%%%발",
        "씨ㅣㅣ발",
        "😀씨앗🌱",
    ],
)
def test_benign_or_unreviewed_variants_do_not_create_strikes(text):
    assert ProfanityFilter().scan(text).decision == "NO_MATCH"


def test_modern_jamo_views_agree():
    assert jamo_view("씨발") == jamo_view("씨발") == "ㅆㅣㅂㅏㄹ"


def test_tampered_lexicon_fails_closed(tmp_path):
    path = tmp_path / "lexicon.json"
    path.write_text('{"sha256":"wrong","lexicon_version":"lexicon-v1"}', encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        ProfanityFilter(path)


def test_empty_review_only_and_starter_production_gates(tmp_path):
    from pathlib import Path

    import app.profanity

    source = Path(app.profanity.__file__).resolve().parent / "resources/lexicon.json"
    for rules in (
        [],
        [
            {
                "rule_id": "candidate",
                "key": ["ㅆ"],
                "scope": "EXACT_TOKEN",
                "separator_after_keys": [],
                "evidence": "REVIEW_ONLY",
            }
        ],
    ):
        payload = {
            "lexicon_version": "lexicon-v1",
            "normalization_version": "test",
            "starter_only": False,
            "rules": rules,
        }
        payload["sha256"] = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        path = tmp_path / "lexicon.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="confirmed"):
            ProfanityFilter(path)
    with pytest.raises(ValueError, match="starter"):
        ProfanityFilter(source, production=True)
