"""API Ingress and Egress Event Envelopes.

Architectural References:
- UA-ADP-01: Decoupled POST 202 + resumable SSE event stream (UA-D1, UA-D2)
- UA-ADP-04: Typed event envelope with sequence numbering (UA-D7, UA-D8)
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
import uuid
from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    """Supported SSE event stream frame types."""
    HEARTBEAT = "heartbeat"
    STATUS = "status"
    TOKEN = "token"
    ACTION_REQUESTED = "action_requested"
    FINAL = "final"
    ERROR = "error"


class ClientTurnRequest(BaseModel):
    """Client turn submission payload (POST /api/v1/conversations/{id}/turns)."""
    conversation_id: str = Field(..., min_length=1, description="Unique conversation UUID")
    idempotency_key: str = Field(..., min_length=8, description="Client-generated deduplication key")
    message: str = Field(..., min_length=1, description="Customer turn message text")
    channel: str = Field(default="web", description="Originating channel; web-only in v1 per UA-D1")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Client session metadata")

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        if v.lower() != "web":
            raise ValueError("Only 'web' channel is supported in v1 per UA-D1")
        return v.lower()


class EventEnvelope(BaseModel):
    """Typed event envelope delivered across the resumable SSE wire protocol."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Globally unique event ID")
    conversation_id: str = Field(..., min_length=1, description="Associated conversation ID")
    sequence_num: int = Field(..., ge=1, description="Monotonically incrementing turn sequence number")
    event_type: EventType = Field(..., description="Classification of stream event")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event data body")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of emission")

    def to_sse_event(self) -> str:
        """Format envelope as a standard SSE frame with id, event, and data lines."""
        data_json = self.model_dump_json()
        return f"id: {self.sequence_num}\nevent: {self.event_type.value}\ndata: {data_json}\n\n"


class TurnResponseAccepted(BaseModel):
    """HTTP 202 Accepted response payload returned immediately upon ingress."""
    conversation_id: str = Field(..., description="Active conversation identifier")
    stream_uri: str = Field(..., description="Resumable SSE stream endpoint URI")
    idempotency_key: str = Field(..., description="Echoed client idempotency key")
    status: str = Field(default="accepted", description="Ingress status")
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Ingress timestamp")
