"""Tiered Principal Identity and RFC 8693 On-Behalf-Of Tokens.

Architectural References:
- UA-ADP-02: Tiered identity assurance (T0 -> T1 -> T2) and RFC 8693 delegation (UA-D3, UA-D4, UA-D9)
- UA-D9: User access token valid 24 hours to outlive the 12-hour session cap
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, field_validator, model_validator


class IdentityTier(str, Enum):
    """Tiered identity assurance levels per UA-D3."""
    T0_ANONYMOUS = "T0_anon"
    T1_OTP = "T1_otp"
    T2_SSO = "T2_sso"


class PrincipalContext(BaseModel):
    """Authenticated caller context attached to dialogue sessions."""
    principal_id: str = Field(..., min_length=1, description="Unique customer principal ID")
    tenant_id: str = Field(..., min_length=1, description="Tenant organization ID")
    tier: IdentityTier = Field(default=IdentityTier.T0_ANONYMOUS, description="Assurance tier")
    roles: List[str] = Field(default_factory=lambda: ["customer"], description="Assigned enterprise roles")
    step_up_verified: bool = Field(default=False, description="Flag indicating step-up MFA completion")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Context creation time")

    def can_access_private_data(self) -> bool:
        """T0 anonymous principals are strictly restricted to public KB per KR-D5."""
        return self.tier != IdentityTier.T0_ANONYMOUS

    def can_mutate_account(self) -> bool:
        """Account modifications require at least T1 OTP or T2 SSO."""
        return self.tier in (IdentityTier.T1_OTP, IdentityTier.T2_SSO)


class OnBehalfOfToken(BaseModel):
    """RFC 8693 OAuth 2.0 Token Exchange On-Behalf-Of token structure per UA-D4."""
    jti: str = Field(default_factory=lambda: str(uuid.uuid4()), description="JWT ID")
    sub: str = Field(..., min_length=1, description="Subject: original user principal ID")
    act: Dict[str, str] = Field(
        default_factory=lambda: {"sub": "agent:enterprise-support-v1"},
        description="Actor: delegation entity (the agent system)"
    )
    tenant_id: str = Field(..., min_length=1, description="Target enterprise tenant scope")
    scopes: List[str] = Field(default_factory=list, description="Downstream system permission scopes")
    iss: str = Field(default="https://auth.enterprise-agent.internal", description="Token issuer")
    aud: str = Field(default="enterprise-core-apis", description="Intended audience")
    iat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Issued at")
    exp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=24),
        description="Expiration time (24h validity per UA-D9)"
    )

    @model_validator(mode="after")
    def validate_delegation_invariants(self) -> "OnBehalfOfToken":
        # Actor assertion must name the agent
        if "sub" not in self.act or not self.act["sub"].startswith("agent:"):
            raise ValueError("RFC 8693 'act' claim must assert agent delegation authority")
        # Ensure expiration is after issuance
        if self.exp <= self.iat:
            raise ValueError("Token expiration must be after issuance timestamp")
        return self

    @property
    def is_expired(self) -> bool:
        """Check if token is expired relative to current UTC time."""
        return datetime.now(timezone.utc) >= self.exp
