"""
Generate LLD page for Component [2/15]: Knowledge & Retrieval.

Materializes checkpoint.md §6 (loop step 11): boundary, Flow A (offline ingest +
instant purge), Flow B (online query), Flow C (quality loop), one card per
sub-component (mechanic + status), the Known/Unknown failure grid with step-9
effects (after §6.9a), and a decision-log summary.

Only the page `page:lld_knowledge` is replaced; every other page is left untouched.
"""

from lld_layout import PageBuilder, est_h, write_page

PID = "page:lld_knowledge"
TOTAL_W = 2600
START_X = 80
GAP = 30


def build_records():
    pb = PageBuilder(PID, "kr")
    add, connect = pb.add, pb.connect

    def row(y, items, color_default, x0=START_X, width=TOTAL_W, gap=GAP):
        return pb.row(y, items, color_default, x0, width, gap)

    # ── Title ────────────────────────────────────────────────────────────
    add("title",
        "COMPONENT [2/15]: KNOWLEDGE & RETRIEVAL\n"
        "All sources · Nightly batch + instant purge · Public + per-tenant indexes · BM25 + dense (RRF) · Screened LLM rerank · Top-10",
        START_X, 40, TOTAL_W, 70, "blue", size="m", align="middle", vertical_align="middle")

    # ── Boundary ─────────────────────────────────────────────────────────
    y = 130
    h = row(y, [
        ("recv", "RECEIVES (upstream)\n"
                 "• Offline: documents, access rules, deletion events from source systems\n"
                 "• Online: RetrievalRequest from Orchestration (Comp 2)\n"
                 "  with tenant, tier and on-behalf-of token (UA-D3 / D4)", "light-blue", False),
        ("hand", "HANDS OFF (downstream)\n"
                 "• Top-10 passages + citation metadata → Orchestration\n"
                 "• Retrieved text flagged untrusted → Safety (7)\n"
                 "• Retrieval traces → Observability (10), Evaluation (9)\n"
                 "• Content gaps → Improvement (16) · purge tombstones → Cache (12)", "light-green", False),
        ("notown", "DOES NOT OWN\n"
                   "• Packing passages into the prompt (Comp 2, ADP-03)\n"
                   "• Customer facts (4) · live account data = tool calls (5)\n"
                   "• Access policy + screening engine (7) · answer grounding check (9)\n"
                   "• Semantic cache (12) · raw document storage (8) · test sets (9)", "light-red", False),
    ], "light-blue")

    # ── Flow A: offline ingestion + instant purge ────────────────────────
    y += h + 40
    fa_y = y
    inner_x, inner_w = START_X + 30, TOTAL_W - 60
    a_y = fa_y + 45
    ha = row(a_y, [
        ("a1", "① Knowledge Sources (KR-D1)\n• Curated KB · release notes · policies\n• Known bugs · postmortems · runbooks\n• Raw resolved tickets / chats", "light-blue", False),
        ("a2", "② Nightly Batch (KR-D3, D15)\n• Check deletion list → filter\n• Ingest → verify\n• Post-batch deletion check", "light-blue", False),
        ("a3", "③ Clean & Label\n• PII-mask tickets (Comp 7 engine, Q2)\n• audience: internal by default (D13)\n• source: human | agent (D16)", "light-violet", False),
        ("a4", "④ Chunk (KR-D4)\n• Keeps headings, tables, code intact\n• Small child chunks for matching\n• Parent section returned", "light-violet", False),
        ("a5", "⑤ Index (KR-D5)\n• Public index: curated + operational\n• Per-tenant private index: tickets (Q1)\n• BM25 + dense vectors", "light-green", False),
    ], "light-blue", x0=inner_x, width=inner_w, gap=70)
    for i, (s, t) in enumerate([("a1", "a2"), ("a2", "a3"), ("a3", "a4"), ("a4", "a5")]):
        connect(f"a{i}", s, t, color="blue")

    p_y = a_y + ha + 50
    a5_x = inner_x + 4 * ((inner_w - 4 * 70) / 5 + 70)
    purge_w = (inner_w - 4 * 70) / 5 * 2 + 70
    purge_text = ("INSTANT PURGE PATH (KR-Q3)\n"
                  "Deletions · GDPR erasures · access revocations\n"
                  "→ remove chunks + embeddings now (not nightly)\n"
                  "→ tombstone to semantic cache (Comp 12)")
    hp = est_h(purge_text, purge_w)
    add("purge", purge_text, a5_x - purge_w - 70, p_y, purge_w, hp, "red")
    connect("purge", "purge", "a5", fa="R", ta="B", color="red", bend=-20)

    fa_h = (p_y + hp + 25) - fa_y
    pb.frame_behind("flowA", "FLOW A · OFFLINE: INGEST & INDEX (e.g. BUG-8192 filed, later fixed in v4.2.1)",
                    START_X, fa_y, TOTAL_W, fa_h, "blue")

    # ── Flow B: online query ─────────────────────────────────────────────
    y = fa_y + fa_h + 40
    fb_y = y
    r1_y = fb_y + 45
    h1 = row(r1_y, [
        ("b0", "NOT OWNED →\nOrchestration sends RetrievalRequest\n(query, recent turns, tenant, tier,\non-behalf-of token, budget)", None, True),
        ("b1", "① Rewrite (KR-D8)\n• Standalone query from follow-ups\n• Extract exact IDs: v4.2, Err 504\n• No HyDE / multi-query", "light-blue", False),
        ("b2", "② Candidates (KR-D7, D6)\n• BM25 + dense, public + own tenant\n• T0 anonymous: public only\n• Copied ACLs; live check if restricted", "light-blue", False),
        ("b3", "③ Fuse (KR-D7)\n• Reciprocal Rank Fusion, k = 60\n• ~50 candidates", "light-blue", False),
    ], "light-blue", x0=inner_x, width=inner_w, gap=70)

    r2_y = r1_y + h1 + 70
    h2 = row(r2_y, [
        ("b7", "NOT OWNED ←\nOrchestration packs 35% slot (ADP-03)\n→ citation events (UA-D7)\n→ confidence gate (Comp 9)", None, True),
        ("b6", "⑥ Return Top-10 (KR-D10, Q5)\n• Parent sections, always 10\n• Internal chunks dropped (D2, Q4)\n• No 'no evidence' result", "yellow", False),
        ("b5", "⑤ LLM Rerank (KR-D9, D12)\n• Validation wrapper checks IDs / order\n• On failure → fall back to RRF order\n• Fallback rate → Comp 10", "orange", False),
        ("b4", "④ Screen Untrusted Text (KR-D14)\n• Before the reranker reads it\n• Engine + policy owned by Comp 7\n• Agent text labeled 'unverified'", "light-red", False),
    ], "light-blue", x0=inner_x, width=inner_w, gap=70)

    fb_h = (r2_y + h2 + 25) - fb_y
    pb.frame_behind("flowB", "FLOW B · ONLINE QUERY (Sarah @ acme-corp, T2: \"DB failed over after v4.2 upgrade … $12,400 overage\")",
                    START_X, fb_y, TOTAL_W, fb_h, "green")
    for i, (s, t) in enumerate([("b0", "b1"), ("b1", "b2"), ("b2", "b3")]):
        connect(f"q{i}", s, t, color="blue")
    connect("turn", "b3", "b4", fa="B", ta="T", color="grey", label="~50 candidates")
    for i, (s, t) in enumerate([("b4", "b5"), ("b5", "b6"), ("b6", "b7")]):
        connect(f"r{i}", s, t, fa="L", ta="R", color="green")

    # ── Flow C: quality loop ─────────────────────────────────────────────
    y = fb_y + fb_h + 40
    fc_y = y
    hc = row(fc_y + 45, [
        ("c1", "Retrieval traces\nquery · rewrite · candidates · scores · kept 10", "light-blue", False),
        ("c2", "Gap signals (KR-D11)\nLow rerank scores · judge context precision · re-asks · thumbs · offline test set (Comp 9)", "yellow", False),
        ("c3", "NOT OWNED\nContent-gap report → Improvement (16) / specialists (13)", None, True),
        ("c4", "Article written\nInternal by default; marked public explicitly (D13)", "light-violet", False),
        ("c5", "Back to ① Sources\n→ next nightly batch (Flow A)", "light-blue", False),
    ], "light-blue", x0=inner_x, width=inner_w, gap=70)
    for i, (s, t) in enumerate([("c1", "c2"), ("c2", "c3"), ("c3", "c4"), ("c4", "c5")]):
        connect(f"c{i}", s, t, color="violet")
    fc_h = hc + 70
    pb.frame_behind("flowC", "FLOW C · QUALITY LOOP (KCS: gaps become articles)",
                    START_X, fc_y, TOTAL_W, fc_h, "violet")

    # ── Sub-component cards ─────────────────────────────────────────────
    y = fc_y + fc_h + 40
    add("sub_hdr", "SUB-COMPONENTS · MECHANIC + STATUS", START_X, y, TOTAL_W, 45, "blue",
        size="m", align="middle", vertical_align="middle")
    y += 60
    hs = row(y, [
        ("s1", "KNOWLEDGE SOURCES\nRegistry: owner, trust, audience, tenant scope, product version.\n\n✔ D1 All source tiers\n✔ D2 Only customer-visible cited\n✔ D13 Internal by default\n✔ D16 Human / agent provenance", "light-green", False),
        ("s2", "INGESTION\nSync, parse, mask, dedupe, label; propagate deletions.\n\n✔ D3 Nightly batch\n✔ Q3 Instant purge path\n✔ Q2 PII-mask tickets\n✔ D15 Deletion-aware batch", "light-green", False),
        ("s3", "INDEXING\nChunk, embed, build BM25 + dense indexes per layout.\n\n✔ D4 Structure-aware, parent-child\n✔ D5 Public + per-tenant private\n✔ Q1 Tickets tenant-private", "light-green", False),
        ("s4", "RETRIEVAL\nRewrite, find candidates, enforce access.\n\n✔ D6 Copied ACLs + live check\n✔ D7 BM25 + dense, RRF\n✔ D8 Standalone rewrite + IDs\n✔ D14 Screen before rerank", "light-green", False),
        ("s5", "RANKING\nFuse, rerank, cut off, package citations.\n\n✔ D9 LLM reranker\n✔ D12 Validation + RRF fallback\n✔ D10 Always top-10 (Q5)\n✔ Q4 Per-chunk audience filter", "light-green", False),
        ("s6", "RETRIEVAL EVALUATION\nIn-pipeline quality signals; offline harness is Comp 9.\n\n✔ D11 Test set + live signals + sampled LLM judge\n(no 'no evidence' rate, per D10)", "light-green", False),
    ], "light-green")

    # ── Failure grid ────────────────────────────────────────────────────
    y += hs + 40
    add("fail_hdr", "FAILURE MODES (KNOWN / UNKNOWN) · WITH STEP-9 EFFECT", START_X, y, TOTAL_W, 45, "red",
        size="m", align="middle", vertical_align="middle")
    y += 60
    q = {
        "q1": "Q1 · KNOWN KNOWNS (contract breaches)\n"
              "• KK1 Nightly batch fails silently / half-completes → OWNED (monitoring → Comp 10)\n"
              "• KK2 Query hits another tenant's private index → MITIGATED (tenant from token, D5)\n"
              "• KK3 Reranker output malformed → FIXED (D12 validation + RRF fallback)\n"
              "• KK4 Anonymous request reaches a private index → FIXED (D5 + public-only T0 token)\n"
              "• KK5 Embedding model changed without re-index → OWNED\n"
              "• KK6 Purge misses copies → FIXED for indexes; judge samples / test sets OWNED (Comp 9)",
        "q2": "Q2 · KNOWN UNKNOWNS (magnitude unknown)\n"
              "• KU1 Fusion settings wrong for this corpus → MITIGATED (measured by D11)\n"
              "• KU2 LLM rerank + screening latency / cost → OWNED (Comp 11 / 12)\n"
              "• KU3 Tickets with wrong recorded fixes → MITIGATED (one tenant only; judge)\n"
              "• KU4 Masking hides IDs the search needs → OWNED (measure on test set)\n"
              "• KU5 Query rewrite drifts → OWNED (measured by D11)\n"
              "• KU6 A day of staleness on incident day → OWNED, accepted (D3)",
        "q3": "Q3 · UNKNOWN KNOWNS (tacit conventions)\n"
              "• UK1 Wrong product version docs → MITIGATED only if version is used as a filter\n"
              "• UK2 Outdated articles never removed → OWNED (needs 'superseded' status)\n"
              "• UK3 Colleague's ticket surfaces for Sarah → OWNED, real v1 exposure (per-user rules → Comp 7)\n"
              "• UK4 Unlabeled chunk becomes citable → FIXED (D13 internal by default)\n"
              "• UK5 Test set goes stale → MITIGATED (live signals + judge)",
        "q4": "Q4 · UNKNOWN UNKNOWNS (emergent from combined decisions)\n"
              "• UU1 Planted ticket instructs the LLM reranker → MITIGATED (D14 screening first)\n"
              "• UU2 Real but irrelevant citations look grounded (D10 × D9) → OWNED → Comp 9 gate\n"
              "• UU3 Mask placeholders distort search (Q2 × D7) → OWNED\n"
              "• UU4 Erased data re-ingested by nightly batch → FIXED (D15 deletion-aware)\n"
              "• UU5 Agent's own replies become evidence → MITIGATED (D16 labeled 'unverified')",
    }
    half = (TOTAL_W - GAP) / 2
    top_h = max(est_h(q["q1"], half), est_h(q["q2"], half))
    bot_h = max(est_h(q["q3"], half), est_h(q["q4"], half))
    add("q1", q["q1"], START_X, y, half, top_h, "light-blue")
    add("q2", q["q2"], START_X + half + GAP, y, half, top_h, "orange")
    add("q3", q["q3"], START_X, y + top_h + GAP, half, bot_h, "light-violet")
    add("q4", q["q4"], START_X + half + GAP, y + top_h + GAP, half, bot_h, "light-red")

    # ── Decision log summary ────────────────────────────────────────────
    y += top_h + GAP + bot_h + 40
    dl_y = y
    y += 40
    hd = row(y, [
        ("dl1", "CONFIRMED (16 decisions + 7 follow-ups, none open)\n"
                "✔ D1 All sources · D2 cite customer-visible only · D3 nightly batch\n"
                "✔ D4 structure-aware + parent-child · D5 public + per-tenant indexes\n"
                "✔ D6 copied ACLs + live check · D7 BM25 + dense RRF · D8 standalone rewrite\n"
                "✔ D9 LLM reranker · D10 always top-10 · D11 test set + live + judge\n"
                "✔ D12 rerank validation · D13 internal default · D14 screen before rerank\n"
                "✔ D15 deletion-aware batch · D16 human / agent provenance (agent labeled)", "green", False),
        ("dl2", "HAND-OFFS TO OTHER COMPONENTS\n"
                "Comp 9: judge relevance, not citation presence (UU2); discount agent-written evidence (UU5)\n"
                "Comp 7: per-user rules on tickets (UK3); masking that keeps IDs searchable (UU3)\n"
                "Comp 12: model versions + rerank cost (KK5, KU2) · Comp 8: durable deletion list\n\n"
                "OWNED RISKS TO WATCH\n"
                "UK3 colleague's ticket · UU2 grounded-looking answers · KU4 masked IDs · KU6 staleness\n\n"
                "Full reasoning: checkpoint.md §6", "yellow", False),
    ], "green", x0=START_X + 20, width=TOTAL_W - 40, gap=GAP)
    pb.frame_behind("declog", "DECISION LOG SUMMARY (checkpoint.md §6.6 – §6.10)",
                    START_X, dl_y, TOTAL_W, hd + 60, "green")

    return pb.records("LLD - [2] Knowledge & Retrieval", "a4")


def main():
    write_page(*build_records())


if __name__ == "__main__":
    main()
