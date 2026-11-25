"use client";
// Right-hand panels (execution trace, citation). Everything shown is
// already filtered by the server for the caller's tier; nothing here hides data client-side.
import { useEffect, useRef, useState, type ReactNode } from "react";

import type { Citation } from "@/lib/reduce.ts";
import type { Agent, Trace, ToolCall } from "@/lib/trace.ts";
import { Icon, type IconName } from "./icons.tsx";

export type Loadable<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; value: T };

const GOOD = new Set(["SUCCESS", "PASS", "MASKED", "NONE FOUND", "SKIPPED"]);

export function Drawer({ title, icon, onClose, children }: {
  title: string; icon?: IconName; onClose: () => void; children: ReactNode;
}) {
  const close = useRef<HTMLButtonElement>(null);
  const latest = useRef(onClose);
  latest.current = onClose;
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && latest.current();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, []);
  return (
    <aside role="dialog" aria-labelledby="drawer-title"
      className="fixed inset-0 z-40 flex flex-col bg-surface lg:static lg:z-auto lg:w-[460px] lg:shrink-0 lg:border-l lg:border-line">
      <header className="flex items-center gap-3 border-b border-line px-5 py-4">
        {icon && <Icon name={icon} className="size-6 text-muted" />}
        <h2 id="drawer-title" className="flex-1 truncate text-lg font-semibold">{title}</h2>
        <button ref={close} onClick={onClose} aria-label="Close panel"
          className="rounded-md p-1.5 text-muted hover:bg-subtle hover:text-ink focus-visible:outline-2 focus-visible:outline-brand">
          <Icon name="x" />
        </button>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5">{children}</div>
    </aside>
  );
}

function Pending<T>({ item, retry, children }: { item: Loadable<T>; retry?: () => void; children: (value: T) => ReactNode }) {
  if (item.state === "loading") return <p className="py-8 text-center text-sm text-muted" role="status">Loading…</p>;
  if (item.state === "error") {
    return (
      <div className="rounded-lg border border-line bg-subtle p-4 text-sm text-muted" role="alert">
        <p>{item.message}</p>
        {retry && <button onClick={retry} className="mt-2 font-medium text-brand hover:underline">Try again</button>}
      </div>
    );
  }
  return <>{children(item.value)}</>;
}

// Execution trace ----------------------------------------------------------------------------------------

const word = (status: Agent["status"]) => ({ success: "SUCCESS", failed: "FAILED", stopped: "STOPPED" })[status];

function treeLines(trace: Trace) {
  const [coordinator, ...specialists] = trace.agents;
  const children = [
    ...specialists.map((agent) => `${agent.name} · ${word(agent.status)} (${agent.outcome})`),
    `${trace.final.label} · ${trace.final.outcome}`,
  ];
  return [
    "Request",
    `└─ ${coordinator?.name ?? "Coordinator"} · ${coordinator ? word(coordinator.status) : ""}`,
    ...children.map((line, i) => `   ${i === children.length - 1 ? "└─" : "├─"} ${line}`),
  ];
}

function Result({ value }: { value: string }) {
  const good = GOOD.has(value);
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[11px] font-semibold ${good
      ? "border-ok/40 bg-ok-soft text-ok" : "border-bad/40 bg-bad-soft text-bad"}`}>{value}</span>
  );
}

function StatusMark({ status }: { status: Agent["status"] }) {
  const tone = status === "success" ? "bg-ok" : "bg-bad";
  return (
    <span className={`grid size-6 shrink-0 place-items-center rounded-full text-white ${tone}`}>
      <Icon name={status === "success" ? "check" : "x"} className="size-3.5" />
    </span>
  );
}

function Call({ call }: { call: ToolCall }) {
  const args = Object.entries(call.args);
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">{call.name}</p>
          {args.map(([key, value]) => (
            <p key={key} className="truncate font-mono text-xs text-muted" title={value}>{key}: {value}</p>
          ))}
        </div>
        <Result value={call.result} />
      </div>
      {call.items.length > 0 && (
        <details className="mt-2 border-t border-line pt-2" open>
          <summary className="flex cursor-pointer items-center gap-1 font-mono text-xs text-muted">
            <Icon name="chevron" className="chevron size-3.5 transition-transform" /> Results ({call.items.length})
          </summary>
          <ul className="mt-1 space-y-0.5 pl-5 font-mono text-xs">
            {call.items.map((item, i) => <li key={i} className="truncate">{item}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function AgentCard({ agent }: { agent: Agent }) {
  const n = agent.calls.length;
  const subtitle = n ? `${n} tool call${n === 1 ? "" : "s"} · ${agent.outcome}` : agent.outcome;
  return (
    <details className="group rounded-xl border border-line bg-subtle/60" open={agent.id === "knowledge"}>
      <summary className="flex cursor-pointer items-center gap-3 px-4 py-3">
        <StatusMark status={agent.status} />
        <span className="min-w-0 flex-1">
          <span className="block font-semibold">{agent.name}</span>
          <span className="block truncate text-xs text-muted">{subtitle}</span>
        </span>
        <Icon name="chevron" className="chevron size-4 text-muted transition-transform" />
      </summary>
      <div className="space-y-2 px-4 pb-4">
        <p className="text-xs text-muted">{agent.role}</p>
        {agent.calls.map((call, i) => <Call key={i} call={call} />)}
        {agent.notes.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted">
            {agent.notes.map((note, i) => <li key={i}>{note}</li>)}
          </ul>
        )}
      </div>
    </details>
  );
}

export function TraceView({ item, retry }: { item: Loadable<Trace>; retry: () => void }) {
  return (
    <Pending item={item} retry={retry}>
      {(trace) => {
        const shared = Object.entries(trace.shared_state);
        return (
          <div className="space-y-4">
            <p className="text-sm text-muted">{trace.summary}</p>
            {(trace.models?.length ?? 0) > 0 && (
              <p className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm">
                <Icon name="spark" className="size-4 shrink-0 text-brand" />
                <span className="text-muted">{trace.models!.length > 1 ? "Models used" : "Model used"}</span>
                <span className="break-all font-mono text-xs">{trace.models!.join(", ")}</span>
              </p>
            )}
            {trace.detail === "summary" && (
              <p className="rounded-lg border border-brand/30 bg-brand-soft px-3 py-2 text-xs text-brand-strong">
                Sign in with an email code to see each step, its tool calls and the shared state.
              </p>
            )}
            <section aria-labelledby="tree-title" className="rounded-xl border border-line p-4">
              <h3 id="tree-title" className="mb-2 flex items-center gap-2 font-semibold">
                <Icon name="tree" className="size-5 text-brand" /> Execution tree
              </h3>
              <pre className="overflow-x-auto font-mono text-xs leading-6 text-ink">{treeLines(trace).join("\n")}</pre>
            </section>
            {trace.agents.map((agent) => <AgentCard key={agent.id} agent={agent} />)}
            {shared.length > 0 && (
              <details className="rounded-xl border border-line">
                <summary className="flex cursor-pointer items-center gap-2 px-4 py-3">
                  <span className="flex-1 font-semibold">Shared state ({shared.length} items)</span>
                  <Icon name="chevron" className="chevron size-4 text-muted transition-transform" />
                </summary>
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 px-4 pb-4 font-mono text-xs">
                  {shared.map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="text-muted">{key}</dt>
                      <dd className="break-words">{value === null ? "—" : String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
            <p className="text-xs text-muted">
              This view describes the steps and the models used, never scores or internal settings.
            </p>
          </div>
        );
      }}
    </Pending>
  );
}

// Citation -----------------------------------------------------------------------------------------------

export function CitationView({ citation }: { citation: Citation }) {
  const [tab, setTab] = useState<"preview" | "details">("preview");
  const tabs = [["preview", "Preview"], ["details", "Details"]] as const;
  return (
    <div>
      <div role="tablist" className="mb-4 flex gap-6 border-b border-line">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-1 pb-2 text-sm font-medium ${tab === id
              ? "border-brand text-brand" : "border-transparent text-muted hover:text-ink"}`}>{label}</button>
        ))}
      </div>
      {tab === "preview" ? (
        <section>
          <h3 className="mb-2 font-semibold">Excerpt</h3>
          {citation.heading && <p className="mb-1 text-sm font-medium">{citation.heading}</p>}
          <p className="whitespace-pre-line text-sm italic leading-relaxed text-ink/90">
            {citation.excerpt || "No preview is available for this source."}
          </p>
        </section>
      ) : (
        <dl className="space-y-3 text-sm">
          {([["Title", citation.title], ["Section", citation.heading], ["Document id", citation.doc_id]] as const)
            .filter(([, value]) => value)
            .map(([label, value]) => (
              <div key={label}>
                <dt className="text-muted">{label}</dt>
                <dd className={label === "Document id" ? "font-mono" : ""}>{value}</dd>
              </div>
            ))}
        </dl>
      )}
    </div>
  );
}
