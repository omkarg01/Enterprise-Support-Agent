"""Unit tests for Envelope, Identity, and Context models (Gate for task-1-envelope-identity)."""

import os
import sys
from datetime import datetime, timedelta, timezone
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from enterprise_agent.models.envelope import (
    ClientTurnRequest,
    EventEnvelope,
    EventType,
    TurnResponseAccepted,
)
from enterprise_agent.models.identity import (
    IdentityTier,
    OnBehalfOfToken,
    PrincipalContext,
)
from enterprise_agent.models.context import (
    ContextEnvelope,
    DialogueSlot,
    ProfileSlot,
    RAGSlot,
    ScratchpadSlot,
    SystemSlot,
)


class TestEnvelopeModels(unittest.TestCase):
    """Test suite for ClientTurnRequest, EventEnvelope, and TurnResponseAccepted."""

    def test_client_turn_request_valid(self):
        req = ClientTurnRequest(
            conversation_id="conv-12345",
            idempotency_key="idem-key-9999",
            message="How do I reset my API key?",
            channel="web",
        )
        self.assertEqual(req.conversation_id, "conv-12345")
        self.assertEqual(req.channel, "web")

    def test_client_turn_request_rejects_non_web_channel(self):
        with self.assertRaises(ValueError):
            ClientTurnRequest(
                conversation_id="conv-12345",
                idempotency_key="idem-key-9999",
                message="Hello",
                channel="telephony",
            )

    def test_event_envelope_sse_serialization(self):
        env = EventEnvelope(
            conversation_id="conv-12345",
            sequence_num=1,
            event_type=EventType.STATUS,
            payload={"phase": "TRIAGED", "state": "RESOLVING"},
        )
        sse_str = env.to_sse_event()
        self.assertIn("id: 1\n", sse_str)
        self.assertIn("event: status\n", sse_str)
        self.assertIn("data: ", sse_str)
        self.assertIn("conv-12345", sse_str)

    def test_turn_response_accepted_schema(self):
        resp = TurnResponseAccepted(
            conversation_id="conv-abc",
            stream_uri="/api/v1/conversations/conv-abc/events",
            idempotency_key="idem-abc-123",
        )
        self.assertEqual(resp.status, "accepted")
        self.assertIn("/api/v1/conversations/conv-abc/events", resp.stream_uri)


class TestIdentityModels(unittest.TestCase):
    """Test suite for Tiered Identity and RFC 8693 On-Behalf-Of Tokens."""

    def test_principal_context_permissions(self):
        t0 = PrincipalContext(principal_id="user-anon", tenant_id="tenant-1", tier=IdentityTier.T0_ANONYMOUS)
        self.assertFalse(t0.can_access_private_data())
        self.assertFalse(t0.can_mutate_account())

        t1 = PrincipalContext(principal_id="user-otp", tenant_id="tenant-1", tier=IdentityTier.T1_OTP)
        self.assertTrue(t1.can_access_private_data())
        self.assertTrue(t1.can_mutate_account())

        t2 = PrincipalContext(principal_id="user-sso", tenant_id="tenant-1", tier=IdentityTier.T2_SSO, step_up_verified=True)
        self.assertTrue(t2.can_access_private_data())
        self.assertTrue(t2.can_mutate_account())

    def test_rfc_8693_on_behalf_of_token(self):
        token = OnBehalfOfToken(
            sub="usr-enterprise-42",
            tenant_id="tenant-acme",
            scopes=["tickets:read", "tickets:write"],
        )
        self.assertEqual(token.sub, "usr-enterprise-42")
        self.assertTrue(token.act["sub"].startswith("agent:"))
        self.assertFalse(token.is_expired)

        # Check 24-hour expiration duration
        delta = token.exp - token.iat
        self.assertAlmostEqual(delta.total_seconds(), 24 * 3600, delta=5)

    def test_rfc_8693_rejects_invalid_actor(self):
        with self.assertRaises(ValueError):
            OnBehalfOfToken(
                sub="usr-42",
                tenant_id="tenant-1",
                act={"sub": "malicious-third-party"},
            )


class TestContextSlotModels(unittest.TestCase):
    """Test suite for ADP-03 Tripartite Slot Allocator."""

    def test_context_envelope_quotas(self):
        envelope = ContextEnvelope(
            total_token_budget=10000,
            system_slot=SystemSlot(persona="Support Agent", token_count=1000),
            profile_slot=ProfileSlot(principal_id="u-1", tenant_id="t-1", tier="T1", token_count=1000),
            rag_slot=RAGSlot(token_count=3000),
            dialogue_slot=DialogueSlot(token_count=2000),
            scratchpad_slot=ScratchpadSlot(token_count=800),
        )
        quotas = envelope.quotas
        self.assertEqual(quotas["system"], 1500)
        self.assertEqual(quotas["profile"], 1500)
        self.assertEqual(quotas["rag"], 3500)
        self.assertEqual(quotas["dialogue"], 2500)
        self.assertEqual(quotas["scratchpad"], 1000)

        self.assertEqual(envelope.total_tokens, 7800)
        self.assertTrue(envelope.is_within_budget())


if __name__ == "__main__":
    unittest.main()
