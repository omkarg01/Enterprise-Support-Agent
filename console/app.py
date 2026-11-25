"""The specialist console (FR-39 / HL-ADP-05; Streamlit in v1, in place of the regional Retool).

    streamlit run console/app.py

Queues, most urgent first; take an item; an action approval shows its card, with one checkbox per field and
the Approve button disabled until every field is checked, and a rejection needs a reason code; a hand-off
shows the packet, a reply box whose warnings need a justification to send anyway, draft feedback with
failure codes, and hand back. Every action signs in again for that item (sign_in), and every rule is
enforced by the server modules (hitl.queues, hitl.approval, hitl.handoff), not by this page.

The services come from st.session_state["services"] (tests set it) or from build_services(), which needs
DATABASE_URL and, until the console's OIDC redirect exists, a development identity provider.
"""

import os
from dataclasses import dataclass
from typing import Any

import streamlit as st

from hitl.approval import RejectReason, Submission
from hitl.handoff import DraftFailure, Justification
from hitl.queues import QUEUES, Reason


@dataclass
class Services:
    tenant_id: str
    staff_id: str
    queues: Any      # hitl.queues.Queues
    desk: Any        # hitl.approval.Desk
    handoffs: Any    # hitl.handoff.Handoffs
    sign_in: Any     # (staff_id, item_id) -> hitl.approval.StaffCredential


def build_services():  # pragma: no cover - needs a running database
    from channels.identity.oidc import MockIdP
    from data.pool import pool
    from hitl.approval import CONSOLE_CLIENT, Desk, StaffCredential, staff_verifier
    from hitl.handoff import Handoffs
    from hitl.queues import Queues
    from safety.authz.cedar import Authorizer
    from tools.validation.approvals import ApprovalGate

    connect = pool(os.environ["DATABASE_URL"], "console").connection  # this process's own, at most 2
    tenant_id = os.environ.get("CONSOLE_TENANT", "t_us")
    idp = MockIdP()  # development only: stands in for the SSO redirect

    def sign_in(staff_id, item_id):
        token = idp.id_token(CONSOLE_CLIENT, tenant_id, staff_id, str(item_id))
        return StaffCredential(tenant_id, str(item_id), token)
    verify = staff_verifier(connect, idp.issuer, idp.public_key)
    log = lambda *item: print("console:", *item)  # noqa: E731
    queues = Queues(connect, None, None, verify, alert=log, notify=log)
    gate = ApprovalGate(connect, None, Authorizer(), verify)
    return Services(tenant_id, os.environ.get("CONSOLE_STAFF", ""), queues, Desk(connect, gate, verify),
                    Handoffs(connect, queues, verify, alert=log), sign_in)


FAILED = object()


def attempt(action, *args, **kwargs):
    """The action's result, or FAILED after showing why the server refused it."""
    try:
        return action(*args, **kwargs)
    except (PermissionError, ValueError) as error:
        st.error(str(error))
        return FAILED


def approval(s, item_id):
    card = attempt(s.desk.card, s.tenant_id, item_id, s.sign_in(s.staff_id, item_id))
    if card is FAILED:
        return
    st.subheader(f"Approve {card.confirm['tool_id']} ({card.level})")
    st.write(card.packet["summary"])
    st.json(card.packet["proposed_action"], expanded=False)
    st.caption("Check every field against the evidence before approving.")
    checked = {name: st.checkbox(f"{name}: {value}", key=f"confirm-{item_id}-{name}")
               for name, value in card.confirm.items()}
    if st.button("Approve", key="approve", disabled=not all(checked.values()), type="primary"):
        submission = Submission(verdict="approve", confirmed=dict(card.confirm))
        if attempt(s.desk.decide, s.tenant_id, item_id, s.sign_in(s.staff_id, card.request_id),
                   submission) is not FAILED:
            st.success("Approved")
    reason = st.selectbox("Reject because", list(RejectReason), index=None, key="reject-reason",
                          format_func=lambda code: code.value)
    if st.button("Reject", key="reject", disabled=reason is None):
        attempt(s.desk.decide, s.tenant_id, item_id, s.sign_in(s.staff_id, card.request_id),
                Submission(verdict="reject", reason_code=reason))


def handoff(s, item_id):
    st.subheader("Conversation")
    text = st.text_area("Reply to the customer", key=f"reply-{item_id}")
    justification = st.selectbox("Send anyway because", list(Justification), index=None, key="justification",
                                 format_func=lambda code: code.value)
    if st.button("Send", key="send", disabled=not text):
        sent = attempt(s.handoffs.reply, s.tenant_id, item_id, s.sign_in(s.staff_id, item_id), text,
                       justification)
        if sent is not FAILED and not sent.sent:
            for warning in sent.warnings:
                st.warning(warning)
    verdict = st.radio("The agent's draft was", ["accepted", "edited", "rejected"], index=None, key="verdict")
    failure = st.selectbox("Failure", list(DraftFailure), index=None, key="failure",
                           format_func=lambda code: code.value)
    if st.button("Record feedback", key="feedback",
                 disabled=verdict is None or (verdict != "accepted") != (failure is not None)):
        attempt(s.handoffs.feedback, s.tenant_id, item_id, s.sign_in(s.staff_id, item_id), verdict,
                None if failure is None else failure.value)
    if st.button("Hand back to the agent", key="hand-back"):
        attempt(s.handoffs.hand_back, s.tenant_id, item_id, s.sign_in(s.staff_id, item_id))


def main():
    st.title("Specialist console")
    if "services" not in st.session_state:
        st.session_state["services"] = build_services()
    s = st.session_state["services"]
    s.staff_id = st.sidebar.text_input("Staff id", value=s.staff_id, key="staff")
    queue = st.sidebar.selectbox("Queue", QUEUES, key="queue")
    for item_id, reason, acuity, due, status in s.queues.waiting(s.tenant_id, queue):
        left, right = st.columns([4, 1])
        left.write(f"{reason} · urgency {acuity} · due {due:%H:%M} · {status}")
        if status == "waiting" and right.button("Take", key=f"take-{item_id}"):
            take = s.queues.claim if reason == Reason.ACTION_APPROVAL_REQUIRED.value else s.handoffs.pick_up
            if attempt(take, s.tenant_id, item_id, s.sign_in(s.staff_id, item_id)) is not FAILED:
                st.session_state["open"] = (item_id, reason)
    if "open" in st.session_state:
        item_id, reason = st.session_state["open"]
        (approval if reason == Reason.ACTION_APPROVAL_REQUIRED.value else handoff)(s, item_id)


main()
