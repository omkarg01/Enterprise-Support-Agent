"""Context Envelope and Slot Allocator Models.

Architectural References:
- ADP-03: Tripartite Structured Slot Allocator (15% System, 15% Profile, 35% RAG, 25% Dialogue, 10% Scratchpad)
- MS-ADP-01: Working memory compaction and slot budget management
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class SystemSlot(BaseModel):
    """Immutable System Persona, Boundaries, and Hard Safety Invariants (15% quota)."""
    persona: str = Field(..., description="Enterprise agent role description")
    safety_invariants: List[str] = Field(default_factory=list, description="Hard negative constraints")
    active_tools_schema: Optional[str] = Field(default=None, description="Serialized schema of available tools")
    token_count: int = Field(default=0, ge=0, description="Estimated token count")


class ProfileSlot(BaseModel):
    """Customer Identity, Account Facts, and Chronic Preferences (15% quota)."""
    principal_id: str = Field(..., description="Customer principal ID")
    tenant_id: str = Field(..., description="Tenant identifier")
    tier: str = Field(..., description="Identity tier (T0, T1, T2)")
    facts: List[Dict[str, Any]] = Field(default_factory=list, description="Extracted long-term facts")
    token_count: int = Field(default=0, ge=0, description="Estimated token count")


class RAGSlot(BaseModel):
    """Grounded RAG Knowledge Chunks retrieved from canonical sources (35% quota)."""
    chunks: List[Dict[str, Any]] = Field(default_factory=list, description="Retrieved parent-child document chunks")
    retrieval_query: str = Field(default="", description="Standalone transformed query string")
    token_count: int = Field(default=0, ge=0, description="Estimated token count")


class DialogueSlot(BaseModel):
    """Chronological Dialogue Buffer containing recent verbatim turns (25% quota)."""
    turns: List[Dict[str, str]] = Field(default_factory=list, description="Recent verbatim user and assistant turns")
    rolling_summary: Optional[str] = Field(default=None, description="Compacted earlier dialogue turns")
    token_count: int = Field(default=0, ge=0, description="Estimated token count")


class ScratchpadSlot(BaseModel):
    """Ephemeral Working Memory Buffer for intermediate ReAct reasoning (10% quota)."""
    thoughts: List[str] = Field(default_factory=list, description="Intermediate reasoning traces")
    pending_tool_calls: List[Dict[str, Any]] = Field(default_factory=list, description="Unexecuted tool call plans")
    token_count: int = Field(default=0, ge=0, description="Estimated token count")


class ContextEnvelope(BaseModel):
    """Complete assembled context window conforming to ADP-03 slot allocations."""
    total_token_budget: int = Field(default=8192, gt=0, description="Total allocated model context budget")
    system_slot: SystemSlot
    profile_slot: ProfileSlot
    rag_slot: RAGSlot
    dialogue_slot: DialogueSlot
    scratchpad_slot: ScratchpadSlot

    @property
    def quotas(self) -> Dict[str, int]:
        """Compute maximum token quotas for each slot based on fixed percentages."""
        return {
            "system": int(self.total_token_budget * 0.15),
            "profile": int(self.total_token_budget * 0.15),
            "rag": int(self.total_token_budget * 0.35),
            "dialogue": int(self.total_token_budget * 0.25),
            "scratchpad": int(self.total_token_budget * 0.10),
        }

    @property
    def total_tokens(self) -> int:
        """Total tokens across all active slots."""
        return (
            self.system_slot.token_count
            + self.profile_slot.token_count
            + self.rag_slot.token_count
            + self.dialogue_slot.token_count
            + self.scratchpad_slot.token_count
        )

    def is_within_budget(self) -> bool:
        """Verify whether overall context fits within total allocated token budget."""
        return self.total_tokens <= self.total_token_budget
