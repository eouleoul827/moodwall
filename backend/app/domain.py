"""Pure policies: no HTTP, database or queue imports."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import regex

KST = ZoneInfo("Asia/Seoul")
MENTION_RETENTION_SECONDS = 3600
WHITE_SPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680"
    + "".join(chr(i) for i in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000"
)
EMOJI = {
    "party": {"emoji": "🎉", "label": "축하"},
    "heart": {"emoji": "❤️", "label": "하트"},
    "sparkles": {"emoji": "✨", "label": "반짝임"},
    "rain": {"emoji": "🌧️", "label": "비"},
    "cloud": {"emoji": "☁️", "label": "구름"},
    "fire": {"emoji": "🔥", "label": "불꽃"},
    "leaf": {"emoji": "🌿", "label": "나뭇잎"},
    "star": {"emoji": "⭐", "label": "별"},
}
ANIMATIONS = {
    "CELEBRATION": "fireworks",
    "JOY": "hearts",
    "SUPPORT": "sparkles",
    "EXCITEMENT": "petals",
    "TIRED": "clouds",
    "SADNESS": "rain",
    "ANGER": "embers",
    "NEUTRAL": "float",
}
INFLIGHT = ("PENDING", "PROCESSING")
CONSUMING = (*INFLIGHT, "APPROVED")


def business_day(now: datetime):
    return now.astimezone(KST).date()


def midnight_after(now: datetime):
    return datetime.combine(business_day(now) + timedelta(days=1), time(), KST).astimezone(timezone.utc)


def normalize_content(value: str) -> str:
    if len(value.encode("utf-8")) > 2048:
        raise ValueError("content too large")
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cc", "Cs", "Zl", "Zp"} or (category == "Cf" and character != "\u200d"):
            raise ValueError("unsupported control character")
    result = unicodedata.normalize("NFC", value.strip(WHITE_SPACE))
    visible = regex.sub(r"\p{Default_Ignorable_Code_Point}", "", result, timeout=0.05)
    if not any(unicodedata.category(character)[0] in "LNPS" for character in visible):
        raise ValueError("content must contain a visible base character")
    if not 1 <= len(regex.findall(r"\X", result, timeout=0.05)) <= 30:
        raise ValueError("content must be 1..30 grapheme clusters")
    if len(result.encode("utf-8")) > 2048:
        raise ValueError("content too large")
    return result


def request_fingerprint(content: str, emoji_id: str) -> bytes:
    canonical = json.dumps(
        {"content": content, "emojiId": emoji_id, "version": "1"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).digest()


def serialize_submission(row: dict) -> dict:
    messages = {
        "PENDING": "검수를 기다리고 있어요.",
        "PROCESSING": "내용을 확인하고 있어요.",
        "APPROVED": "전광판에 보낼 준비가 되었어요.",
        "REJECTED": "작성 규칙을 확인해 주세요.",
        "NEEDS_REWRITE": "공개하기 적절한 내용으로 다시 작성해 주세요.",
        "FAILED": "처리하지 못했어요. 잠시 후 다시 작성해 주세요.",
        "EXPIRED": "처리 가능한 시간이 지났어요.",
        "CANCELLED": "작성 요청이 취소되었어요.",
    }
    return {
        "id": str(row["id"]),
        "businessDate": row["business_date"],
        "moderationStatus": row["moderation_status"],
        "displayStatus": row["display_status"],
        "content": row["content"],
        "emojiId": row["emoji_id"],
        "emotion": row["emotion"],
        "animationId": ANIMATIONS.get(row["emotion"]),
        "createdAt": row["accepted_at"],
        "expiresAt": row.get("content_delete_at")
        or row.get("pre_display_deadline_at")
        or row["accepted_at"] + timedelta(seconds=MENTION_RETENTION_SECONDS),
        "deadlineAt": row["expires_at"],
        "finalizedAt": row["finalized_at"],
        "message": messages[row["moderation_status"]],
    }
