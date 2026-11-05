from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ReasonCode = Literal[
    "INAPPROPRIATE_CONTENT", "FALSE_POSITIVE", "MAINTENANCE", "DEVICE_REPLACED", "SECURITY_REVIEW", "OTHER"
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Admission(Input):
    ticket: str = Field(min_length=1, max_length=2048)


class Mention(Input):
    content: str = Field(min_length=1)
    emojiId: str = Field(min_length=1, max_length=50)


class MentionV2(Input):
    content: str = Field(min_length=1, max_length=2048)


class ReceiptLookup(Input):
    idempotencyKey: UUID


class ContentReport(Input):
    category: Literal["HARASSMENT", "PRIVACY", "THREAT", "OTHER"]
    submissionId: UUID | None = None


class ResolveReport(Input):
    resolution: Literal["HIDDEN", "BOARD_PAUSED", "NO_ACTION", "INSUFFICIENT_EVIDENCE"]


class ProviderPause(Input):
    paused: bool


class PlaybackClaim(Input):
    clientInstanceId: UUID
    deviceGeneration: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    playbackEpoch: str | None = Field(default=None, pattern=r"^(0|[1-9][0-9]{0,18})$")


class PlaybackCommand(Input):
    clientInstanceId: UUID
    deviceGeneration: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    playbackEpoch: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")


class PlaybackInterrupt(PlaybackCommand):
    reason: Literal["HIDDEN", "PAGE_HIDDEN", "RELOAD", "FRAME_GAP", "ASSET_FAILURE", "NETWORK", "CLIENT_ERROR"]


class Pair(Input):
    code: str = Field(min_length=1, max_length=200)


class Login(Input):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class Heartbeat(Input):
    clientInstanceId: UUID
    displayProtocolVersion: Literal["2"] | None = None


class DeviceMode(Input):
    mode: Literal["MOODWALL", "CHATBOT"]


class OperatorMode(Input):
    mode: Literal["MOODWALL", "CHATBOT", "PAUSED"]
    reasonCode: ReasonCode


class Reason(Input):
    reasonCode: ReasonCode


class Intake(Reason):
    paused: bool


class Empty(Input):
    pass


class DisplayDiagnostic(Input):
    component: Literal["SESSION", "QR", "PLAYBACK", "CACHE", "RENDER"]
    code: Literal[
        "CONNECTION_FAILED",
        "AUTH_REQUIRED",
        "QR_UNAVAILABLE",
        "PLAYBACK_OWNED",
        "PLAYBACK_INTERRUPTED",
        "PLAYBACK_UNKNOWN",
        "ASSET_FAILURE",
        "STORAGE_UNAVAILABLE",
        "RENDER_FAILURE",
    ]
    state: Literal["FAILED", "RECOVERED"]
    version: Literal["1"]
    count: int = Field(ge=1, le=1000)
    occurredAt: AwareDatetime
