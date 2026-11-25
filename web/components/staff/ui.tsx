"use client";
// The staff console's shell and shared pieces (/staffs). Deliberately unlike the customer portal: a light top
// bar with tabs instead of the dark rail, slate and indigo instead of teal, dense tables instead of cards.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";

import { statusLabel as serviceLabel } from "@/lib/services.ts";
import { categoryLabel, statusLabel, teamLabel } from "@/lib/tickets.ts";

const TABS = [
  { href: "/staffs", label: "Overview" },
  { href: "/staffs/tickets", label: "Tickets" },
  { href: "/staffs/customers", label: "Customers" },
  { href: "/staffs/services", label: "Services" },
  { href: "/staffs/handoffs", label: "Hand-offs" },
  { href: "/staffs/feedback", label: "Feedback" },
];

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const current = (href: string) => href === "/staffs" ? path === "/staffs" : path.startsWith(href);
  return (
    <div className="min-h-dvh bg-slate-100 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <p role="note" className="bg-amber-100 px-4 py-1.5 text-center text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        Demo console: no sign-in. Anyone with this link sees every customer&apos;s data and can change it.
      </p>
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 pt-3">
          <Link href="/staffs" className="flex items-center gap-2 pb-3 font-semibold tracking-tight">
            <span className="grid size-7 place-items-center rounded-md bg-indigo-600 text-sm text-white" aria-hidden="true">N</span>
            Nimbus Support <span className="font-normal text-slate-500 dark:text-slate-400">· Staff console</span>
          </Link>
          <nav aria-label="Staff console" className="-mb-px flex gap-1 overflow-x-auto">
            {TABS.map((tab) => (
              <Link key={tab.href} href={tab.href} aria-current={current(tab.href) ? "page" : undefined}
                className={`whitespace-nowrap border-b-2 px-3 pb-3 pt-1 text-sm font-medium ${current(tab.href)
                  ? "border-indigo-600 text-indigo-700 dark:border-indigo-400 dark:text-indigo-300"
                  : "border-transparent text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"}`}>
                {tab.label}
              </Link>
            ))}
          </nav>
          <span className="ml-auto hidden pb-3 text-xs text-slate-500 sm:block dark:text-slate-400">Signed in as demo staff</span>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
    </div>
  );
}

export function PageTitle({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{sub}</p>}
      </div>
      {children}
    </div>
  );
}

export const panel = "rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900";
export const input = "rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-950";
export const th = "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400";
export const td = "px-3 py-2.5 align-top";
const BUTTONS = {
  primary: "bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400",
  plain: "border border-slate-300 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800",
  danger: "border border-rose-300 bg-white text-rose-700 hover:bg-rose-50 dark:border-rose-800 dark:bg-slate-900 dark:text-rose-300 dark:hover:bg-rose-950",
};
export function Button({ tone = "plain", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: keyof typeof BUTTONS;
}) {
  return <button {...props} className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${BUTTONS[tone]} ${className}`} />;
}

const TICKET_TONES: Record<string, string> = {
  open: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  in_review: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  resolved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  rejected: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  closed: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};
export function TicketStatus({ status }: { status: string }) {
  return <span className={`inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ${TICKET_TONES[status] ?? TICKET_TONES.closed}`}>{statusLabel(status)}</span>;
}

// Status colour with its label and a shape, never colour alone.
const SERVICE_TONES = {
  running: ["bg-emerald-500", "text-emerald-700 dark:text-emerald-300", "●"],
  busy: ["bg-amber-500 animate-pulse", "text-amber-700 dark:text-amber-300", "◐"],
  stopped: ["bg-slate-400", "text-slate-600 dark:text-slate-400", "○"],
} as const;
export function ServiceState({ status }: { status: string }) {
  const key = status === "running" ? "running" : status === "stopped" ? "stopped" : "busy";
  const [dot, ink] = SERVICE_TONES[key];
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium ${ink}`}>
      <span className={`size-2 rounded-full ${dot}`} aria-hidden="true" />{serviceLabel(status)}{key === "busy" && "…"}
    </span>
  );
}

export const category = (c: string) => categoryLabel(c);
export const queue = (q: string) => teamLabel(q);
export const when = (at: string | null | undefined) =>
  at ? new Date(at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";

export function Select({ label, value, onChange, options }: {
  label: string; value: string; onChange: (value: string) => void; options: [string, string][];
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-500 dark:text-slate-400">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} className={`${input} text-slate-900 dark:text-slate-100`}>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </label>
  );
}

export function Search({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs font-medium text-slate-500 dark:text-slate-400">
      Search
      <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className={`${input} text-slate-900 dark:text-slate-100`} />
    </label>
  );
}

export function Problem({ children }: { children: ReactNode }) {
  return <p role="alert" className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">{children}</p>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className={`${panel} px-6 py-10 text-center`}>
      <p className="font-medium">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-lg text-sm text-slate-500 dark:text-slate-400">{children}</p>}
    </div>
  );
}

// Loads once and on reload(); with pollMs, also every pollMs while the tab is visible, so a forgotten tab
// does not keep the database (Neon's free compute) awake.
export function useLoad<T>(key: string, load: () => Promise<T>, pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let live = true;
    const run = () => load()
      .then((value) => { if (live) { setData(value); setError(null); } })
      .catch((e: Error) => { if (live) setError(e.message); });
    run();
    if (!pollMs) return () => { live = false; };
    const tick = () => { if (!document.hidden) run(); };
    const timer = setInterval(tick, pollMs);
    document.addEventListener("visibilitychange", tick);
    return () => { live = false; clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, round, pollMs]);
  return { data, error, reload: () => setRound((r) => r + 1) };
}

export function Loading({ error }: { error: string | null }) {
  return error ? <Problem>Could not load this: {error}</Problem> : <p className="text-sm text-slate-500">Loading…</p>;
}
