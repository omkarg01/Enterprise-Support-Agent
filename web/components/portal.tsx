"use client";
// The support portal: identity rail, the conversation, and the trust panels (citations, trace).
// Token and conversation live in sessionStorage: the server binds a conversation to the sign-in session.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { ChatClient, HttpError, readClaims, type Claims, type Conversation, type Vote } from "@/lib/chat-client.ts";
import { addUserMessage, initialState, reduce, type ChatState, type Citation, type Message } from "@/lib/reduce.ts";
import { SERVICE_CARDS, servicesPath, type ServiceCard } from "@/lib/services.ts";
import { ticketsPath, type TicketCard } from "@/lib/tickets.ts";
import type { Trace } from "@/lib/trace.ts";
import { Icon, Logo, type IconName } from "./icons.tsx";
import { CitationView, Drawer, TraceView, type Loadable } from "./panels.tsx";
import { ServiceCardView, ServicesView } from "./services.tsx";
import { TicketCardView, TicketDetailView, TicketsView } from "./tickets.tsx";

const TICKET_CARDS = new Set(["ticket_proposal", "ticket", "sign_in"]);

const LIMIT = 16384; // channels.api.envelope.TurnRequest
const DISCLOSURE = "You are chatting with an AI assistant. A person can take over at any time."; // safety/governance/disclosure.py
const HELP_URL = process.env.NEXT_PUBLIC_HELP_CENTER_URL;
const SUGGESTIONS = ["How do I reset my password?", "How do refunds work?", "Where can I find my invoices?"];

const HISTORY_OPEN = "support-agent:history-open"; // the History drop-down stays as the customer left it

type Panel = { kind: "trace"; turnId: string } | { kind: "citation"; citation: Citation } | null;
type Otp = { stage: "email" | "code"; email: string; code: string; challengeId?: string; busy?: boolean; error?: string };

const store = {
  read(key: string) {
    try { return JSON.parse(sessionStorage.getItem(key) ?? "null"); } catch { return null; }
  },
  write(key: string, value: unknown) {
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: not kept */ }
  },
};

const clock = (at?: number) => at ? new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";

// view: Services, the chat, or My tickets (ticketId: one ticket's page). Each is its own route; the
// conversation is restored from sessionStorage when the customer comes back to the chat.
export default function Portal({ tenantId, view = "chat", ticketId }: {
  tenantId: string; view?: "services" | "chat" | "tickets"; ticketId?: string;
}) {
  const client = useRef<ChatClient | null>(null);
  const stream = useRef<AbortController | null>(null);
  const storageKey = `support-agent:${tenantId}:session`;
  const [claims, setClaims] = useState<Claims | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatState>(initialState);
  const [lost, setLost] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [panel, setPanel] = useState<Panel>(null);
  const [traces, setTraces] = useState<Record<string, Loadable<Trace>>>({});
  const [votes, setVotes] = useState<Record<string, Vote>>({});
  const [otp, setOtp] = useState<Otp>({ stage: "email", email: "", code: "" });
  const [railOpen, setRailOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [pastChats, setPastChats] = useState<Conversation[] | "error" | null>(null);
  const thread = useRef<HTMLDivElement>(null);
  const signIn = useRef<HTMLDialogElement>(null);
  const router = useRouter();

  const remember = useCallback((conversation: string) => {
    store.write(storageKey, { token: client.current!.token, conversationId: conversation });
  }, [storageKey]);

  // resume: send the conversation so far (the server replays its transcript from Last-Event-ID 0).
  const open = useCallback((conversation: string, resume = false) => {
    stream.current?.abort();
    const controller = new AbortController();
    stream.current = controller;
    const api = client.current!;
    api.lastEventId = resume ? "0" : null;
    setConversationId(conversation);
    setChat(initialState());
    setTraces({});
    setVotes({});
    setPanel(null);
    setLost(false);
    remember(conversation);
    api.votes(conversation).then(setVotes, () => { /* no thumbs shown as given; voting again answers 409 */ });
    api.listen(conversation, (event) => setChat((state) => reduce(state, { ...event, at: Date.now() })),
      { signal: controller.signal }).catch(() => {
      if (!controller.signal.aborted) setLost(true);
    });
  }, [remember]);

  const fresh = useCallback(async () => {
    const api = client.current!;
    await api.signInAnonymous(tenantId);
    setClaims(readClaims(api.token));
    setOtp({ stage: "email", email: "", code: "" });
    open(api.uuid());
  }, [open, tenantId]);

  useEffect(() => {
    client.current = new ChatClient();
    const saved = store.read(storageKey);
    const savedClaims = readClaims(saved?.token ?? null);
    if (savedClaims && savedClaims.tid === tenantId && savedClaims.exp * 1000 > Date.now() + 60_000) {
      client.current.token = saved.token;
      setClaims(savedClaims);
      open(saved.conversationId, true);
    } else {
      fresh().catch(() => setLost(true));
    }
    return () => stream.current?.abort();
  }, [fresh, open, storageKey, tenantId]);

  const loadHistory = useCallback(() => {
    client.current?.conversations().then(setPastChats, () => setPastChats("error"));
  }, []);

  useEffect(() => setHistoryOpen(store.read(HISTORY_OPEN) === true), []);

  useEffect(() => {
    if (historyOpen && claims) loadHistory();
  }, [historyOpen, claims, conversationId, loadHistory]);

  function toggleHistory() {
    store.write(HISTORY_OPEN, !historyOpen);
    setHistoryOpen(!historyOpen);
  }

  function pick(conversation: string) {
    setRailOpen(false);
    if (view !== "chat") {
      remember(conversation);
      router.push(`/t/${encodeURIComponent(tenantId)}`);
    } else if (conversation !== conversationId) {
      open(conversation, true);
    }
  }

  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "smooth" });
  }, [chat]);

  async function send(text: string) {
    const content = text.trim();
    if (!content || sending || !conversationId) return;
    setSending(true);
    setInput("");
    setChat((state) => addUserMessage(state, content, Date.now()));
    try {
      await client.current!.send(conversationId, content);
      if (historyOpen) loadHistory(); // a new conversation shows up, titled by its first message
    } catch (error) {
      setInput(content);
      setChat((state) => ({ ...state, error: `Not sent (${(error as Error).message}). Your text is back in the box.` }));
    } finally {
      setSending(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    send(input);
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send(input);
    }
  }

  async function loadTrace(turnId: string) {
    setPanel({ kind: "trace", turnId });
    if (traces[turnId]?.state === "ready") return;
    setTraces((all) => ({ ...all, [turnId]: { state: "loading" } }));
    try {
      const value = await client.current!.trace(conversationId!, turnId);
      setTraces((all) => ({ ...all, [turnId]: { state: "ready", value } }));
    } catch (error) {
      const message = error instanceof HttpError && error.status === 404
        ? "The record of this answer is still being written. Try again in a moment."
        : "Could not load how this was answered.";
      setTraces((all) => ({ ...all, [turnId]: { state: "error", message } }));
    }
  }

  // The first vote is final. Returns the line shown under the reply.
  async function rate(turnId: string, signal: Vote, comment = "") {
    try {
      const result = await client.current!.rate(conversationId!, turnId, signal, comment);
      setVotes((all) => ({ ...all, [turnId]: signal }));
      return result.comment === "dropped"
        ? "Thanks. Your comment had personal details in it, so we kept only your rating."
        : "Thanks for the feedback.";
    } catch (error) {
      if (!(error instanceof HttpError && error.status === 409)) return "Could not send your rating. Try again.";
      client.current!.votes(conversationId!).then(setVotes, () => {});
      return "You already rated this reply.";
    }
  }

  async function startOtp(event: FormEvent) {
    event.preventDefault();
    setOtp((o) => ({ ...o, busy: true, error: undefined }));
    try {
      const challengeId = await client.current!.startOtp(tenantId, otp.email.trim());
      setOtp((o) => ({ ...o, stage: "code", challengeId, busy: false }));
    } catch {
      setOtp((o) => ({ ...o, busy: false, error: "Could not send a code. Check the address and try again." }));
    }
  }

  async function verifyOtp(event: FormEvent) {
    event.preventDefault();
    setOtp((o) => ({ ...o, busy: true, error: undefined }));
    try {
      await client.current!.verifyOtp(tenantId, otp.challengeId!, otp.code.trim());
      setClaims(readClaims(client.current!.token));
      remember(conversationId!);
      setTraces({}); // the trace shows more once signed in
      setOtp({ stage: "email", email: "", code: "" });
      signIn.current?.close();
    } catch {
      setOtp((o) => ({ ...o, busy: false, error: "That code did not work. Check it, or send a new one." }));
    }
  }

  async function raiseTicket(proposal: string) {
    await client.current!.confirmTicket(conversationId!, proposal);
  }

  async function confirmAction(offer: string) {
    await client.current!.confirmAction(conversationId!, offer);
  }

  function goSignIn() {
    signIn.current?.showModal();
    signIn.current?.querySelector("input")?.focus(); // the field, not the Close button
  }

  function newChat() {
    setRailOpen(false);
    const conversation = client.current!.uuid();
    if (view === "chat") return open(conversation);
    remember(conversation);
    router.push(`/t/${encodeURIComponent(tenantId)}`);
  }

  async function logout() {
    try { await client.current!.logout(); } catch { /* the session is dropped locally either way */ }
    await fresh().catch(() => setLost(true));
  }

  const anonymous = !claims || claims.tier === "T0_ANON";
  const navItem = "flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px]";
  const current = "border-l-[3px] border-brand bg-brand/20 font-medium";

  const rail = (
    <nav aria-label="Portal" className="rail flex h-full w-[264px] flex-col bg-rail text-rail-ink">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-5 pb-3">
      <div className="mb-7 flex items-center gap-3 px-1">
        <Logo className="size-9 text-rail-ink" />
        <div>
          <p className="text-lg font-semibold leading-tight">Support Portal</p>
          <p className="text-xs text-rail-muted">Enterprise Support</p>
        </div>
      </div>
      <p className="mb-2 px-1 text-[11px] font-semibold tracking-wider text-rail-muted">NAVIGATION</p>
      <ul className="space-y-1">
        <li>
          <Link href={servicesPath(tenantId)} aria-current={view === "services" ? "page" : undefined}
            className={`${navItem} ${view === "services" ? current : "hover:bg-rail-raised"}`}>
            <Icon name="server" /> Services
          </Link>
        </li>
        <li>
          <button onClick={newChat} className={`${navItem} w-full hover:bg-rail-raised`}>
            <Icon name="plus" /> New conversation
          </button>
        </li>
        <li>
          <button onClick={toggleHistory} aria-expanded={historyOpen} aria-controls="history-list"
            className={`${navItem} w-full hover:bg-rail-raised`}>
            <Icon name="history" /> History
            <Icon name="chevron" className={`ml-auto size-4 transition-transform ${historyOpen ? "rotate-180" : ""}`} />
          </button>
          {historyOpen && (
            <ul id="history-list" aria-label="Past conversations" className="mt-1 max-h-64 space-y-0.5 overflow-y-auto pl-4">
              {pastChats === null || pastChats === "error" || pastChats.length === 0 ? (
                <li className="px-3 py-1.5 text-xs text-rail-muted">
                  {pastChats === null ? "Loading…" : pastChats === "error" ? "Could not load your conversations."
                    : "No past conversations yet."}
                </li>
              ) : pastChats.map((chat) => {
                const here = view === "chat" && chat.id === conversationId;
                return (
                  <li key={chat.id}>
                    <button onClick={() => pick(chat.id)} title={chat.title ?? undefined}
                      aria-current={here ? "true" : undefined}
                      className={`block w-full truncate rounded-md px-3 py-1.5 text-left text-sm ${here
                        ? "bg-brand/20 font-medium" : "text-rail-muted hover:bg-rail-raised hover:text-rail-ink"}`}>
                      {chat.title ?? "Untitled conversation"}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </li>
        <li>
          <Link href={ticketsPath(tenantId)} aria-current={view === "tickets" && !ticketId ? "page" : undefined}
            className={`${navItem} ${view === "tickets" ? current : "hover:bg-rail-raised"}`}>
            <Icon name="ticket" /> My tickets
          </Link>
        </li>
        {HELP_URL && (
          <li>
            <a href={HELP_URL} target="_blank" rel="noreferrer" className={`${navItem} hover:bg-rail-raised`}>
              <Icon name="help" /> Help center <Icon name="external" className="ml-auto size-4" />
            </a>
          </li>
        )}
      </ul>

      </div>

      <div className="border-t border-rail-line px-4 py-3">
        <button onClick={logout} className={`${navItem} w-full hover:bg-rail-raised`}>
          <Icon name="logout" /> Log out
        </button>
      </div>
    </nav>
  );

  return (
    <div className="flex h-dvh overflow-hidden">
      <div className="hidden md:block">{rail}</div>
      {railOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden">
          {rail}
          <button aria-label="Close menu" className="flex-1 bg-black/40" onClick={() => setRailOpen(false)} />
        </div>
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-3 sm:px-6">
          <button aria-label="Open menu" onClick={() => setRailOpen(true)} className="rounded-md p-1.5 text-muted md:hidden">
            <Icon name="menu" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold">
              {view === "services" ? "Services" : view === "chat" ? "Chat" : ticketId ? `Ticket ${ticketId}` : "My tickets"}
            </h1>
            <p className="hidden truncate text-sm text-muted sm:block">
              {view === "services" ? "Your servers and container apps, their status, and what the assistant changed."
                : view === "chat" ? "Get answers from our documentation and knowledge base."
                : "The tickets you raised with our teams, and everything that happened on them."}
            </p>
          </div>
          {anonymous ? (
            <button onClick={goSignIn}
              className="flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong">
              <Icon name="user" className="size-4" /> Sign in
            </button>
          ) : (
            <span className="flex min-w-0 items-center gap-1.5 text-sm text-muted" aria-label={`Signed in as ${claims?.sub}`}>
              <Icon name="user" className="size-4 shrink-0" /><span className="hidden truncate sm:inline">{claims?.sub}</span>
            </span>
          )}
        </header>

        {view === "services" && (
          <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            <div className="mx-auto max-w-3xl">
              <ServicesView client={claims ? client.current : null} signedIn={!anonymous} />
            </div>
          </div>
        )}

        {view === "tickets" && (
          <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
            <div className="mx-auto max-w-3xl">
              {ticketId
                ? <TicketDetailView client={claims ? client.current : null} tenantId={tenantId} ticketId={ticketId}
                    signedIn={!anonymous} />
                : <TicketsView client={claims ? client.current : null} tenantId={tenantId} signedIn={!anonymous} />}
            </div>
          </div>
        )}

        {view === "chat" && <>
        <div ref={thread} className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-5">
            {chat.messages.length === 0 && !chat.status && (
              <div className="py-10 text-center">
                <Icon name="spark" className="mx-auto mb-3 size-8 text-brand" />
                <p className="font-medium">How can we help?</p>
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((text) => (
                    <button key={text} onClick={() => send(text)}
                      className="rounded-full border border-line bg-surface px-3.5 py-1.5 text-sm hover:border-brand hover:text-brand">
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {chat.messages.map((message, i) => (
              message.role === "card" && SERVICE_CARDS.has(message.card?.kind)
                ? <ServiceCardView key={i} card={message.card as ServiceCard} servicesHref={servicesPath(tenantId)}
                    onConfirm={confirmAction} />
                : message.role === "card" && TICKET_CARDS.has(message.card?.kind)
                ? <TicketCardView key={i} card={message.card as TicketCard} tenantId={tenantId} onRaise={raiseTicket}
                    onSignIn={goSignIn} />
                : <Bubble key={i} message={message} onTrace={loadTrace}
                    onCite={(citation) => setPanel({ kind: "citation", citation })}
                    vote={message.turnId ? votes[message.turnId] : undefined} onRate={rate} />
            ))}
            {chat.draft && <div className="max-w-[85%] rounded-2xl border border-line bg-surface p-4 opacity-70">{chat.draft}</div>}
            <div aria-live="polite" className="min-h-6">
              {chat.status && (
                <p className="flex items-center gap-2 text-sm text-muted">
                  <span className="flex gap-1" aria-hidden="true">
                    {["[animation-delay:0ms]", "[animation-delay:150ms]", "[animation-delay:300ms]"].map((delay) => (
                      <span key={delay} className={`size-1.5 animate-pulse rounded-full bg-brand ${delay}`} />
                    ))}
                  </span>
                  {chat.status}
                </p>
              )}
            </div>
            {(chat.error || lost) && (
              <div role="alert" className="flex items-center gap-3 rounded-lg border border-bad/40 bg-bad-soft px-4 py-3 text-sm text-bad">
                <span className="flex-1">{lost ? "Disconnected from the conversation." : chat.error}</span>
                {lost && <button onClick={() => fresh().catch(() => setLost(true))} className="font-medium underline">Start again</button>}
              </div>
            )}
          </div>
        </div>

        <form onSubmit={submit} className="mx-4 mb-1.5 rounded-2xl border border-line bg-surface p-3 shadow-sm sm:mx-6">
          <label htmlFor="composer" className="sr-only">Message</label>
          <textarea id="composer" rows={2} maxLength={LIMIT} value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey} placeholder="Type your message…"
            className="block w-full resize-none bg-transparent px-1 py-1 text-[15px] placeholder:text-muted focus:outline-none" />
          <div className="mt-2 flex items-center justify-between">
            <span className="font-mono text-xs text-muted">{input.length} / {LIMIT}</span>
            <button disabled={sending || !input.trim() || !conversationId}
              className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 font-medium text-white hover:bg-brand-strong disabled:opacity-50">
              <Icon name="send" className="size-4" /> {sending ? "Sending" : "Send"}
            </button>
          </div>
        </form>
        <p className="mx-4 mb-3 text-center text-xs text-muted sm:mx-6">{DISCLOSURE}</p>
        </>}
      </main>

      <dialog ref={signIn} aria-labelledby="sign-in-title" onClose={() => setOtp((o) => ({ ...o, error: undefined }))}
        className="m-auto w-[calc(100%-2rem)] max-w-sm rounded-2xl border border-line bg-surface p-6 text-ink shadow-xl backdrop:bg-black/50">
        <div className="mb-4 flex items-center gap-3">
          <Icon name="mail" className="size-5 text-brand" />
          <h2 id="sign-in-title" className="flex-1 text-lg font-semibold">Sign in</h2>
          <button aria-label="Close" onClick={() => signIn.current?.close()} className="rounded-md p-1 text-muted hover:bg-subtle">
            <Icon name="x" className="size-4" />
          </button>
        </div>
        {otp.stage === "email" ? (
          <form onSubmit={startOtp} className="space-y-3">
            <p className="text-sm text-muted">We will email you a one-time code.</p>
            <label htmlFor="otp-email" className="block text-sm font-medium">Work email</label>
            <input id="otp-email" type="email" required autoComplete="email" placeholder="you@company.com"
              value={otp.email} onChange={(e) => setOtp((o) => ({ ...o, email: e.target.value }))}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm placeholder:text-muted focus:border-brand focus:outline-none" />
            <button disabled={otp.busy} className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand py-2 text-sm font-medium text-white hover:bg-brand-strong disabled:opacity-60">
              Send code <Icon name="right" className="size-4" />
            </button>
          </form>
        ) : (
          <form onSubmit={verifyOtp} className="space-y-3">
            <label htmlFor="otp-code" className="block text-sm font-medium">Code sent to {otp.email}</label>
            <input id="otp-code" inputMode="numeric" autoComplete="one-time-code" required autoFocus value={otp.code}
              onChange={(e) => setOtp((o) => ({ ...o, code: e.target.value }))}
              className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm tracking-widest focus:border-brand focus:outline-none" />
            <button disabled={otp.busy} className="w-full rounded-lg bg-brand py-2 text-sm font-medium text-white hover:bg-brand-strong disabled:opacity-60">
              Verify
            </button>
            <button type="button" onClick={() => setOtp({ stage: "email", email: otp.email, code: "" })}
              className="w-full text-xs text-muted hover:text-ink">Use a different address</button>
            {process.env.NODE_ENV !== "production" && (
              <p className="text-xs text-muted">Development: the code is printed in the API log.</p>
            )}
          </form>
        )}
        {otp.error && <p role="alert" className="mt-3 text-sm text-bad">{otp.error}</p>}
      </dialog>

      {panel?.kind === "trace" && (
        <Drawer title="How this was answered" onClose={() => setPanel(null)}>
          <TraceView item={traces[panel.turnId] ?? { state: "loading" }} retry={() => loadTrace(panel.turnId)} />
        </Drawer>
      )}
      {panel?.kind === "citation" && (
        <Drawer title={panel.citation.title ?? "Source"} icon="doc" onClose={() => setPanel(null)}>
          <CitationView key={panel.citation.doc_id} citation={panel.citation} />
        </Drawer>
      )}
    </div>
  );
}

function Bubble({ message, onTrace, onCite, vote, onRate }: {
  message: Message;
  onTrace: (turnId: string) => void;
  onCite: (citation: Citation) => void;
  vote?: Vote;
  onRate: (turnId: string, signal: Vote, comment?: string) => Promise<string>;
}) {
  const [copied, setCopied] = useState(false);
  const [asking, setAsking] = useState(false);    // the thumbs-down box is open
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[85%] rounded-2xl bg-user px-4 py-3 text-white sm:max-w-[70%]">
        <p className="whitespace-pre-wrap break-words">{message.text}</p>
        <p className="mt-1 flex items-center justify-end gap-1 text-xs text-white/70">
          {clock(message.at)} <Icon name="check" className="size-3.5" />
        </p>
      </div>
    );
  }
  if (message.role !== "assistant") {
    return (
      <p className="mx-auto flex items-center gap-2 rounded-full bg-subtle px-4 py-2 text-sm text-muted">
        <Icon name={message.role === "card" ? "doc" : "person"} className="size-4" /> {message.text}
      </p>
    );
  }
  async function send(signal: Vote, text = "") {
    setBusy(true);
    setNote(await onRate(message.turnId!, signal, text));
    setBusy(false);
    setAsking(false);
  }
  const citations = message.citations ?? [];
  const link = "flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted hover:bg-subtle hover:text-ink";
  const action = (icon: IconName, label: string, onClick: () => void) => (
    <button onClick={onClick} className={link}>
      <Icon name={icon} className="size-4" /> {label}
    </button>
  );
  return (
    <article className="max-w-full sm:max-w-[85%]">
      <div className="rounded-2xl border border-line bg-surface p-4 shadow-sm">
        <span className="mb-2 inline-block rounded-full bg-brand px-2.5 py-0.5 text-xs font-medium text-white">AI-assisted</span>
        <p className="whitespace-pre-wrap break-words leading-relaxed">{message.text}</p>
        <p className="mt-2 text-right text-xs text-muted">{clock(message.at)}</p>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {citations.length > 0 && action("sources", `Sources (${citations.length})`, () => onCite(citations[0]))}
        {message.turnId && action("external", "How this was answered", () => onTrace(message.turnId!))}
        <button onClick={() => navigator.clipboard?.writeText(message.text).then(() => setCopied(true))}
          className={link} aria-label="Copy reply">
          <Icon name={copied ? "check" : "copy"} className="size-4" />
        </button>
        {message.turnId && (["up", "down"] as const).map((signal) => (
          <button key={signal} disabled={Boolean(vote) || busy} aria-pressed={vote === signal || (signal === "down" && asking)}
            aria-label={signal === "up" ? "Helpful" : "Not helpful"}
            onClick={() => signal === "down" ? setAsking(true) : send("up")}
            className={`${vote === signal ? link.replace("text-muted", "text-brand") : link} disabled:cursor-default disabled:hover:bg-transparent ${vote && vote !== signal ? "opacity-40" : ""}`}>
            <Icon name={signal === "up" ? "thumbUp" : "thumbDown"} className="size-4" />
          </button>
        ))}
      </div>
      {asking && !vote && (
        <form className="mt-2 rounded-xl border border-line bg-surface p-3"
          onSubmit={(event) => { event.preventDefault(); send("down", comment); }}>
          <label htmlFor={`why-${message.turnId}`} className="text-sm font-medium">What went wrong? (optional)</label>
          <textarea id={`why-${message.turnId}`} value={comment} onChange={(event) => setComment(event.target.value)}
            maxLength={2000} rows={2} placeholder="The answer was wrong, incomplete, or not what I asked"
            className="mt-1 w-full resize-y rounded-lg border border-line bg-surface p-2 text-sm" />
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" onClick={() => setAsking(false)} className={link}>Cancel</button>
            <button type="submit" disabled={busy}
              className="rounded-md bg-brand px-3 py-1 text-sm font-medium text-white disabled:opacity-60">Send feedback</button>
          </div>
        </form>
      )}
      {note && <p role="status" className="mt-1 text-sm text-muted">{note}</p>}
      {citations.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-2" aria-label="Sources">
          {citations.map((citation, i) => (
            <li key={`${citation.doc_id}-${i}`}>
              <button onClick={() => onCite(citation)}
                className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm hover:border-brand">
                <span className="grid size-5 place-items-center rounded bg-brand text-[11px] font-semibold text-white">{i + 1}</span>
                <Icon name="doc" className="size-4 text-muted" />
                <span className="max-w-[16rem] truncate">{citation.title ?? citation.doc_id}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
