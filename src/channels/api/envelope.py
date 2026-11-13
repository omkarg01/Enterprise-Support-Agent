"""Channel-agnostic envelopes (UA-ADP-01 UA-D1) and the typed outbound event contract (UA-ADP-04 UA-D7)."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    STATUS = "status"
    TEXT_DELTA = "text_delta"
    CITATION = "citation"
    ACTION_CARD = "action_card"
    HANDOFF = "handoff"
    FINAL = "final"
    ERROR = "error"


class Principal(BaseModel):
    tenant_id: str
    principal_id: str
    assurance_tier: str = "T0_ANON"
    session_id: str | None = None   # the live session the token names (its sid): tools act through it


class TurnRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16384)
    attachment_refs: list[str] = Field(default_factory=list)


class InboundTurn(BaseModel):
    """What the orchestrator receives for one accepted turn."""

    tenant_id: str
    principal_id: str
    assurance_tier: str
    session_id: str | None = None
    conversation_id: str
    turn_id: str
    channel: str = "WEB_WIDGET"
    content: str
    attachment_refs: list[str]
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
