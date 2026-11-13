"""Tiered sign-in, session-bound access tokens and RFC 8693 on-behalf-of tokens (FR-7; UA-D3, UA-D4, UA-D9).

- Access tokens last 24 h (UA-D9) but are only honoured while their session is live (30 min idle / 12 h), so
  logging out or expiring the session revokes them. They must also match the session's current principal and
  tier, so a token from before a step-up stops working.
- Step-up keeps the session when the person is the same (or was anonymous); otherwise a new session starts.
- On-behalf-of tokens carry sub = user, act = the agent, one audience and only the scopes that audience and
  the tier both allow; they last 15 minutes. T0 gets the read-only public audience only (UA-Q1 ii).
"""

import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from channels import sessions
from channels.api.envelope import Principal
from channels.identity import otp
from channels.identity.oidc import verify_id_token
from channels.identity.tiers import AUDIENCES, SCOPES, StepUpRequired, Tier
from channels.identity.tokens import TokenError, decode, encode

ISSUER = "support-agent"
API_AUDIENCE = "support-agent-api"
AGENT = "svc:enterprise-agent-runtime"
ACCESS_TTL = 24 * 3600
OBO_TTL = 15 * 60


class Identity:
    def __init__(self, connect, signing_key=None, idp=None, client_id="support-agent", send_otp=None):
        """connect() returns an autocommit psycopg connection; idp is the SSO provider's (issuer, key)."""
        self.connect, self.key = connect, signing_key or Ed25519PrivateKey.generate()
        self.idp, self.client_id, self.send_otp = idp, client_id, send_otp

    # Sign-in ---------------------------------------------------------------------------------------------

    def anonymous(self, tenant_id):
        return self._sign_in(tenant_id, f"anon:{uuid.uuid4()}", Tier.T0_ANON, current=None)

    def start_otp(self, tenant_id, user_id):
        with self.connect() as conn:
            return otp.issue(conn, tenant_id, user_id, self.send_otp)

    def verify_otp(self, tenant_id, challenge_id, code, current=None):
        with self.connect() as conn:
            user_id = otp.verify(conn, tenant_id, challenge_id, code)
        return self._sign_in(tenant_id, user_id, Tier.T1_VERIFIED, current)

    def sso(self, tenant_id, id_token, nonce, current=None):
        if self.idp is None:
            raise TokenError("SSO is not configured")
        user_id = verify_id_token(id_token, self.idp[1], self.idp[0], self.client_id, tenant_id, nonce)
        return self._sign_in(tenant_id, user_id, Tier.T2_SSO, current)

    def logout(self, access_token):
        claims = self._claims(access_token)
        with self.connect() as conn:
            sessions.end(conn, claims["tid"], claims["sid"])

    def _sign_in(self, tenant_id, principal_id, tier, current):
        """Return an access token; step up the caller's live session when `current` allows it."""
        with self.connect() as conn:
            claims = self._live_claims(conn, current) if current else None
            same_person = claims and (claims["sub"] == principal_id or claims["tier"] == Tier.T0_ANON.name)
            if same_person and claims["tid"] == tenant_id and Tier[claims["tier"]] <= tier:
                session_id = claims["sid"]
                sessions.raise_tier(conn, tenant_id, session_id, principal_id, tier.name)
            else:
                session_id = sessions.start(conn, tenant_id, principal_id, tier.name)
        now = int(time.time())
        return encode({"iss": ISSUER, "aud": API_AUDIENCE, "sub": principal_id, "tid": tenant_id,
                       "sid": session_id, "tier": tier.name, "scope": " ".join(SCOPES[tier]),
                       "iat": now, "exp": now + ACCESS_TTL}, self.key)

    # Request authentication (plugs into channels.api.app.create_app) ------------------------------------

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "a Bearer access token is required")
        try:
            with self.connect() as conn:
                claims = self._live_claims(conn, header.removeprefix("Bearer "))
                conversation_id = request.path_params.get("conversation_id")
                if conversation_id and not sessions.open_conversation(
                        conn, claims["tid"], claims["sub"], claims["sid"], conversation_id):
                    raise HTTPException(403, "this conversation belongs to someone else")
        except TokenError as error:
            raise HTTPException(401, str(error)) from None
        return Principal(tenant_id=claims["tid"], principal_id=claims["sub"], assurance_tier=claims["tier"],
                         session_id=claims["sid"])

    def _claims(self, token):
        return decode(token, self.key.public_key(), ISSUER, API_AUDIENCE)

    def _live_claims(self, conn, token):
        """Verified claims of a token whose session is live and still matches it; counts as activity."""
        claims = self._claims(token)
        live = sessions.touch(conn, claims["tid"], claims["sid"])
        if live is None:
            raise TokenError("session ended or expired")
        if live != (claims["sub"], claims["tier"]):
            raise TokenError("token was replaced by a step-up")
        return claims

    # RFC 8693 token exchange --------------------------------------------------------------------------

    def exchange(self, access_token, audience):
        """Mint an on-behalf-of token for one downstream audience; StepUpRequired if the tier is short."""
        if audience not in AUDIENCES:
            raise ValueError(f"unknown audience {audience!r}")
        with self.connect() as conn:
            claims = self._live_claims(conn, access_token)
        return self._mint_obo(claims["tid"], claims["sub"], claims["tier"], audience)

    def session_principal(self, tenant_id, session_id):
        """(principal_id, tier name) of a live session, from the server-side session record; counts as
        activity. TokenError when the session has ended, expired or belongs to another tenant."""
        with self.connect() as conn:
            live = sessions.touch(conn, tenant_id, session_id)
        if live is None:
            raise TokenError("session ended or expired")
        return live

    def exchange_for_session(self, tenant_id, session_id, audience):
        """The same on-behalf-of token as exchange(), from a session reference instead of the bearer token,
        so durable workflows carry only the (non-secret) session id. Same tier and scope rules."""
        if audience not in AUDIENCES:
            raise ValueError(f"unknown audience {audience!r}")
        principal_id, tier = self.session_principal(tenant_id, session_id)
        return self._mint_obo(tenant_id, principal_id, tier, audience)

    def _mint_obo(self, tenant_id, principal_id, tier_name, audience):
        minimum, allowed = AUDIENCES[audience]
        tier = Tier[tier_name]
        if tier < minimum:
            raise StepUpRequired(minimum)
        now = int(time.time())
        return encode({"iss": ISSUER, "aud": audience, "sub": principal_id, "act": {"sub": AGENT},
                       "tid": tenant_id, "tier": tier.name,
                       "scope": " ".join(s for s in SCOPES[tier] if s in allowed),
                       "iat": now, "exp": now + OBO_TTL}, self.key)

    def verify_obo(self, token, audience):
        """What a downstream tool calls: claims of a valid on-behalf-of token for its audience."""
        claims = decode(token, self.key.public_key(), ISSUER, audience)
        if claims.get("act", {}).get("sub") != AGENT:
            raise TokenError("not an on-behalf-of token")
        return claims
