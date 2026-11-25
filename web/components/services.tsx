"use client";
// The Services tab (tools.services): the signed-in customer's servers and container apps with their live
// status and what the assistant did on them, and the chat cards for a service action (the offer with its
// Confirm button, then the service after the change). The tab reads the services itself (GET /v1/services),
// so it shows what the system holds, not what the assistant said.
import Link from "next/link";
import { useEffect, useState } from "react";

import type { ChatClient } from "@/lib/chat-client.ts";
import { pollWhileVisible } from "@/lib/poll.ts";
import {
  activityLabel, ago, busy, doneBy, kindLabel, lastAction, offerOf, statusLabel, tone,
  type Service, type ServiceCard, type Services,
} from "@/lib/services.ts";
import { Icon } from "./icons.tsx";

// ponytail: polling; an SSE feed when many customers watch at once. Every 30 s, skipped while the tab is
// hidden and backing off on failure (lib/poll.ts), so an open tab does not keep the API and database busy.
const POLL_MS = 30_000;
const TONES = {
  good: "border-ok/40 bg-ok-soft text-ok",
  busy: "border-warn-line bg-warn-bg text-warn-ink",
  idle: "border-line bg-subtle text-muted",
};
const DOTS = { good: "bg-ok", busy: "bg-[#d97706] animate-pulse", idle: "bg-muted" };

export function ServiceStatus({ status }: { status: string }) {
  const t = tone(status);
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${TONES[t]}`}>
      <span className={`size-2 rounded-full ${DOTS[t]}`} aria-hidden="true" />
      {statusLabel(status)}{busy(status) && "…"}
    </span>
  );
}

function SignInFirst() {
  return (
    <div className="rounded-2xl border border-line bg-surface p-6 text-center">
      <Icon name="server" className="mx-auto mb-3 size-8 text-brand" />
      <p className="font-medium">Sign in to see your services</p>
      <p className="mt-1 text-sm text-muted">Use the email code on the left. Your servers and apps belong to your account.</p>
    </div>
  );
}

function Group({ title, items, latest }: {
  title: string; items: Service[]; latest: ReturnType<typeof lastAction>;
}) {
  if (items.length === 0) return null;
  return (
    <section className="mb-5">
      <h2 className="mb-2 text-sm font-semibold text-muted">{title}</h2>
      <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface" aria-label={title}>
        {items.map((service) => {
          const last = latest[service.name];
          return (
            <li key={service.service_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
              <Icon name={service.kind === "app" ? "box" : "server"} className="size-5 shrink-0 text-brand" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-sm font-medium">{service.name}</span>
                <span className="block text-xs text-muted">
                  {service.region}{service.replicas != null && ` · ${service.replicas} ${service.replicas === 1 ? "replica" : "replicas"}`}
                </span>
              </span>
              {last && (
                <span className="order-last w-full text-xs text-muted sm:order-none sm:w-auto">
                  {doneBy(last)} · {last.action_id} · {ago(last.at)}
                </span>
              )}
              <ServiceStatus status={service.status} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function ServicesView({ client, signedIn }: { client: ChatClient | null; signedIn: boolean }) {
  const [data, setData] = useState<Services | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!client || !signedIn) return;
    let live = true;
    const load = () => client.services()
      .then((value) => { if (live) { setData(value); setFailed(false); } })
      .catch((error) => { if (live) setFailed(true); throw error; });
    load().catch(() => {});
    const stop = pollWhileVisible(load, POLL_MS);
    return () => { live = false; stop(); };
  }, [client, signedIn]);

  if (!signedIn) return <SignInFirst />;
  if (!data) {
    return failed
      ? <p role="alert" className="rounded-lg border border-bad/40 bg-bad-soft px-4 py-3 text-sm text-bad">Could not load your services. Retrying…</p>
      : <p className="text-sm text-muted">Loading…</p>;
  }
  const latest = lastAction(data.activity);
  return (
    <>
      <p className="mb-4 flex items-center gap-2 text-xs text-muted" aria-live="polite">
        <span className={`size-2 rounded-full ${failed ? "bg-bad" : "bg-ok"}`} aria-hidden="true" />
        {failed ? "Connection lost, retrying" : "Live: refreshes every few seconds"}
      </p>
      {data.services.length === 0 && (
        <div className="rounded-2xl border border-line bg-surface p-6 text-center">
          <p className="font-medium">No services on this account</p>
        </div>
      )}
      <Group title="Servers" items={data.services.filter((s) => s.kind === "server")} latest={latest} />
      <Group title="Container apps" items={data.services.filter((s) => s.kind === "app")} latest={latest} />
      <h2 className="mb-2 mt-6 text-sm font-semibold text-muted">Activity</h2>
      {data.activity.length === 0
        ? <p className="text-sm text-muted">Nothing yet. Ask the assistant in the chat to stop, start, restart or scale a service.</p>
        : (
          <ol className="space-y-3 border-l border-line pl-4" aria-label="Activity">
            {data.activity.map((entry) => (
              <li key={entry.action_id} className="relative">
                <span className="absolute -left-[21px] top-1.5 size-2.5 rounded-full bg-brand" aria-hidden="true" />
                <p className="text-sm font-medium">{activityLabel(entry)}</p>
                <p className="text-xs text-muted">
                  {new Date(entry.at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })} · {entry.action_id}, {doneBy(entry) === "Support staff" ? "carried out by support staff" : "confirmed by you in the chat"}
                </p>
              </li>
            ))}
          </ol>
        )}
    </>
  );
}

// A card in the conversation. onConfirm(offer) posts the confirmation; the changed service's card arrives on
// the event stream.
export function ServiceCardView({ card, servicesHref, onConfirm }: {
  card: ServiceCard;
  servicesHref: string;
  onConfirm: (offer: string) => Promise<void>;
}) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const offer = offerOf(card);
  const facts: [string, string | null | undefined][] = [
    ["Service", card.service],
    ["Type", card.service_kind && kindLabel(card.service_kind)],
    ["Region", card.region],
    ["Replicas", card.action === "scale" && card.replicas ? `${card.replicas}` : null],
  ];

  async function confirm() {
    if (!offer) return;
    setState("sending");
    try {
      await onConfirm(offer);
      setState("sent");
    } catch {
      setState("error");
    }
  }

  const done = card.kind === "service";
  return (
    <div className={`max-w-full rounded-2xl border bg-surface p-4 shadow-sm sm:max-w-[85%] ${done ? "border-line" : "border-brand/40"}`}>
      <div className="flex items-center gap-2">
        <Icon name={card.service_kind === "app" ? "box" : "server"} className="size-5 text-brand" />
        <span className="flex-1 font-medium">{card.title}</span>
        {card.status && <ServiceStatus status={card.status} />}
      </div>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {facts.filter(([, value]) => value).map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      {done ? (
        <Link href={servicesHref} className="mt-3 flex items-center gap-1 text-sm font-medium text-brand">
          Open Services <Icon name="right" className="size-4" />
        </Link>
      ) : (
        <>
          <p className="mt-3 text-xs text-muted">Nothing changes until you confirm.</p>
          {offer && (
            <button onClick={confirm} disabled={state === "sending" || state === "sent"}
              className="mt-2 flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong disabled:opacity-60">
              <Icon name={state === "sent" ? "check" : "server"} className="size-4" />
              {state === "sending" ? "Sending…" : state === "sent" ? "Requested" : card.actions?.[0]?.label ?? "Confirm"}
            </button>
          )}
          {state === "error" && <p role="alert" className="mt-2 text-sm text-bad">Could not send that. Try again.</p>}
        </>
      )}
    </div>
  );
}
