"""Finite, versioned starter rules. No claim of comprehensive Korean filtering."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from inference.contracts import RuleEvidence

_INITIAL = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_VOWEL = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_FINAL = "ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
_JAMO = {
    **{chr(0x1100 + i): c for i, c in enumerate(_INITIAL)},
    **{chr(0x1161 + i): c for i, c in enumerate(_VOWEL)},
    **{chr(0x11A8 + i): c for i, c in enumerate(_FINAL)},
}
_SEPARATORS = frozenset(" %.*_-")


def jamo_view(text: str) -> str:
    return "".join(_JAMO.get(char, char) for char in unicodedata.normalize("NFD", text))


def _word(char: str) -> bool:
    return char.isalnum() or unicodedata.category(char).startswith("M")


@dataclass(frozen=True)
class RuleScan:
    lexicon_version: str
    normalization_version: str
    decision: str
    matches: tuple[RuleEvidence, ...]


class ProfanityFilter:
    def __init__(self, path: Path | None = None, *, expected_version: str = "lexicon-v1", production: bool = False):
        path = path or Path(__file__).resolve().parent / "resources" / "lexicon.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        digest = payload.pop("sha256", None)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if digest != hashlib.sha256(canonical).hexdigest():
            raise ValueError("lexicon digest mismatch")
        if set(payload) - {"lexicon_version", "normalization_version", "starter_only", "provenance", "rules"}:
            raise ValueError("unknown lexicon manifest field")
        if payload["lexicon_version"] != expected_version:
            raise ValueError("lexicon version mismatch")
        self.version = payload["lexicon_version"]
        self.normalization_version = payload["normalization_version"]
        self.rules = payload["rules"]
        if production and payload.get("starter_only", True):
            raise ValueError("starter lexicon requires a reviewed production replacement")
        if not self.rules or not any(rule["evidence"] == "CONFIRMED" for rule in self.rules):
            raise ValueError("active confirmed rules required")
        if self.normalization_version != "modern-hangul-jamo-v1" or len(self.rules) > 1000:
            raise ValueError("unsupported normalizer or rule count")
        ids = set()
        for rule in self.rules:
            if set(rule) - {"rule_id", "key", "scope", "separator_after_keys", "optional_insert_after", "evidence"}:
                raise ValueError("unknown rule field")
            RuleEvidence(rule_id=rule["rule_id"], variant_id="exact")
            if rule["rule_id"] in ids or rule["scope"] != "EXACT_TOKEN":
                raise ValueError("duplicate rule or unsupported boundary")
            ids.add(rule["rule_id"])
            if not 1 <= len(rule["key"]) <= 30 or rule["evidence"] not in {"CONFIRMED", "REVIEW_ONLY"}:
                raise ValueError("invalid finite rule")
            if any(not isinstance(k, str) or len(k) != 1 for k in rule["key"]):
                raise ValueError("invalid jamo key")
            if any(not isinstance(i, int) or i < 0 or i >= len(rule["key"]) - 1 for i in rule["separator_after_keys"]):
                raise ValueError("invalid separator position")
            optional = rule.get("optional_insert_after")
            if optional and (
                set(optional) != {"key_index", "token", "max"}
                or optional["key_index"] not in rule["separator_after_keys"]
                or optional["token"] != "ㅣ"
                or optional["max"] != 1
            ):
                raise ValueError("unsupported insertion rule")

    @staticmethod
    def _match(view: str, start: int, rule: dict) -> str | None:
        key = rule["key"]
        optional = rule.get("optional_insert_after")

        def walk(index: int, position: int, skipped: int, inserted: bool) -> str | None:
            if position >= len(view) or view[position] != key[index]:
                return None
            position += 1
            if index == len(key) - 1:
                if position == len(view) or not _word(view[position]):
                    return (
                        "insert-gap"
                        if inserted and skipped
                        else "insert"
                        if inserted
                        else "gap"
                        if skipped
                        else "exact"
                    )
                return None
            options = [(position, skipped, inserted)]
            if (
                optional
                and index == optional["key_index"]
                and not inserted
                and position < len(view)
                and view[position] == optional["token"]
            ):
                options.append((position + 1, skipped, True))
            for next_position, total, had_insertion in options:
                for count in range(3):
                    if count and (
                        index not in rule["separator_after_keys"]
                        or total + count > 4
                        or next_position + count > len(view)
                        or view[next_position + count - 1] not in _SEPARATORS
                    ):
                        break
                    result = walk(index + 1, next_position + count, total + count, had_insertion)
                    if result:
                        return result
            return None

        return walk(0, start, 0, False)

    def scan(self, display_text: str) -> RuleScan:
        # API owns the 30-grapheme rule; this protects standalone adapter callers.
        if len(display_text.encode("utf-8")) > 2048:
            raise ValueError("filter input limit")
        view = jamo_view(display_text)
        confirmed = set()
        candidates = set()
        for rule in self.rules:
            for start, char in enumerate(view):
                if char != rule["key"][0] or (start and _word(view[start - 1])):
                    continue
                variant = self._match(view, start, rule)
                if variant:
                    target = confirmed if rule["evidence"] == "CONFIRMED" else candidates
                    target.add((rule["rule_id"], variant))
        pairs = confirmed if confirmed else candidates
        return RuleScan(
            self.version,
            self.normalization_version,
            "CONFIRMED" if confirmed else "CANDIDATE" if candidates else "NO_MATCH",
            tuple(RuleEvidence(rule_id=r, variant_id=v) for r, v in sorted(pairs)[:5]),
        )
