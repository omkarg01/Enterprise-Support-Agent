"""Channel-agnostic envelopes (UA-ADP-01 UA-D1) and the typed outbound event contract (UA-ADP-04 UA-D7)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class FeedbackRequest(BaseModel):
    signal: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


class TurnRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16384)
    attachment_refs: list[str] = Field(default_factory=list)


class TurnAction(BaseModel):
    """A customer action from a card instead of a typed message: raise a proposed ticket (FR-47), or carry out
    an offered service action (tools.services)."""

    type: Literal["confirm_ticket", "confirm_action"]
    proposal: str = Field(pattern=r"^(TKT|ACT)-[0-9]{5,}$")

    @model_validator(mode="after")
    def matches(self):
        if self.proposal.startswith("TKT-") != (self.type == "confirm_ticket"):
            raise ValueError("confirm_ticket confirms a ticket, confirm_action a service action")
        return self


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
    action: TurnAction | None = None   # set for a card action (the Raise ticket button); content describes it
