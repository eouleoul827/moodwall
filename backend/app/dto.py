from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


class Pair(Input):
    code: str = Field(min_length=1, max_length=200)


class Login(Input):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class Heartbeat(Input):
    clientInstanceId: UUID


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
