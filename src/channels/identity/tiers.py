"""Assurance tiers, what each may do, and which downstream audiences each may reach (UA-D3, UA-D4)."""

from enum import IntEnum


class Tier(IntEnum):
    T0_ANON = 0      # no sign-in: public knowledge and status only
    T1_VERIFIED = 1  # one-time code to the email / phone on file: low-risk reads and case updates
    T2_SSO = 2       # enterprise SSO with MFA: account changes, money, PII


SCOPES = {
    Tier.T0_ANON: ("kb:read", "status:read"),
    Tier.T1_VERIFIED: ("kb:read", "status:read", "tickets:read", "orders:read", "case:update"),
    Tier.T2_SSO: ("kb:read", "status:read", "tickets:read", "orders:read", "case:update",
                  "account:write", "billing:write", "pii:read", "settings:write"),
}

# Downstream audience -> (lowest tier allowed, scopes an on-behalf-of token for it may carry).
AUDIENCES = {
    "public-read-tools": (Tier.T0_ANON, {"kb:read", "status:read"}),
    "status-tools": (Tier.T1_VERIFIED, {"status:read", "tickets:read", "orders:read", "case:update"}),
    # the customer's own account and invoices, read only: from T1 (the user's choice, 2026-11-22: an
    # email-code customer reads their own account records); account and money writes stay at T2
    "account-read-tools": (Tier.T1_VERIFIED, {"tickets:read", "orders:read"}),
    "account-tools": (Tier.T2_SSO, {"tickets:read", "orders:read", "account:write", "billing:write",
                                    "pii:read", "settings:write"}),
}


class StepUpRequired(PermissionError):
    def __init__(self, required):
        super().__init__(f"step-up to {required.name} required")
        self.required = required


def require(tier, required):
    """Raise StepUpRequired unless `tier` (a Tier or its name) reaches `required`."""
    current = Tier[tier] if isinstance(tier, str) else tier
    if current < required:
        raise StepUpRequired(required)
