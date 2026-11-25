"use client";
// The staff console's Overview (ticket counts, who is waiting longest, open work by team and category, service
// health, recent activity) and the Hand-offs page (hitl.queues escalations: empty until hand-offs feed it, W11).
import Link from "next/link";

import { actorLabel, customerHref, didWhat, serviceHealth, staffApi, summary, ticketHref } from "@/lib/staff.ts";
import { activityLabel, ago } from "@/lib/services.ts";
import { Empty, Loading, PageTitle, category, panel, queue, td, th, useLoad, when } from "./ui.tsx";

const POLL_MS = 30_000;  // one /overview call, only while the tab is visible (useLoad)

function Tile({ label, value, sub, href }: { label: string; value: string | number; sub?: string; href?: string }) {
  const body = (
    <>
      <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
    </>
  );
  return href
    ? <Link href={href} className={`${panel} block p-4 hover:border-indigo-300 dark:hover:border-indigo-700`}>{body}</Link>
    : <div className={`${panel} p-4`}>{body}</div>;
}

// One measure, one hue: the label and count carry the meaning, the bar only its size (title = hover text).
function Bars({ title, rows, show }: { title: string; rows: [string, number][]; show: (key: string) => string }) {
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <section className={`${panel} p-4`} aria-label={title}>
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {rows.length === 0 ? <p className="text-sm text-slate-500">No open work.</p> : (
        <ul className="space-y-2.5">
          {rows.map(([key, n]) => (
            <li key={key} title={`${show(key)}: ${n} open`}>
              <div className="flex justify-between text-sm"><span>{show(key)}</span><span className="tabular-nums text-slate-600 dark:text-slate-300">{n}</span></div>
              <div className="mt-1 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
                <div className="h-1.5 rounded-full bg-indigo-500 dark:bg-indigo-400" style={{ width: `${(n / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function OverviewView() {
  const overview = useLoad("overview", staffApi.overview, POLL_MS);
  if (!overview.data) return <><PageTitle title="Overview" /><Loading error={overview.error} /></>;
  const { tickets, services, activity } = overview.data;
  const s = summary(tickets);
  const health = serviceHealth(services);
  return (
    <>
      <PageTitle title="Overview" sub="Every tenant · refreshes every 30 s while this tab is open" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Tile label="Open" value={s.open} href="/staffs/tickets" />
        <Tile label="In review" value={s.inReview} href="/staffs/tickets" />
        <Tile label="Resolved today" value={s.resolvedToday} />
        <Tile label="Rejected" value={s.rejected} />
        <Tile label="Waiting longest" value={s.oldestOpen ? ago(s.oldestOpen.created_at) : "—"}
          sub={s.oldestOpen ? `${s.oldestOpen.ticket_id} · ${s.oldestOpen.principal_id}` : "Nothing open"}
          href={s.oldestOpen ? ticketHref(s.oldestOpen.ticket_id) : undefined} />
      </div>
      <div className="mt-5 grid gap-5 md:grid-cols-3">
        <Bars title="Open work by team" rows={s.byQueue} show={queue} />
        <Bars title="Open work by category" rows={s.byCategory} show={category} />
        <section className={`${panel} p-4`} aria-label="Service health">
          <h2 className="mb-3 text-sm font-semibold">Service health</h2>
          <dl className="grid grid-cols-3 gap-2 text-center">
            {([["Running", health.running, "●", "text-emerald-700 dark:text-emerald-300"],
               ["Changing", health.busy, "◐", "text-amber-700 dark:text-amber-300"],
               ["Stopped", health.stopped, "○", "text-slate-500"]] as const).map(([label, n, mark, ink]) => (
              <div key={label} className="rounded-md bg-slate-50 py-3 dark:bg-slate-800/60">
                <dd className="text-xl font-semibold tabular-nums">{n}</dd>
                <dt className={`text-xs font-medium ${ink}`}><span aria-hidden="true">{mark} </span>{label}</dt>
              </div>
            ))}
          </dl>
          <Link href="/staffs/services" className="mt-3 block text-sm text-indigo-700 hover:underline dark:text-indigo-300">All services →</Link>
        </section>
      </div>
      <h2 className="mb-2 mt-6 text-sm font-semibold">Recent activity</h2>
      {!activity.length ? <Empty title="Nothing has happened yet" /> : (
        <ol className={`${panel} divide-y divide-slate-100 dark:divide-slate-800`}>
          {activity.slice(0, 15).map((a, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-4 py-2.5 text-sm">
              <span className="w-20 shrink-0 text-xs text-slate-500" title={when(a.at)}>{ago(a.at)}</span>
              {a.type === "ticket" ? (
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{actorLabel(a.actor)}</span>{" "}
                  {didWhat(a)}{" "}
                  <Link href={ticketHref(a.ticket_id)} className="font-mono text-indigo-700 hover:underline dark:text-indigo-300">{a.ticket_id}</Link>
                  {a.kind === "status" && a.text && <span className="text-slate-500"> · {a.text}</span>}
                </span>
              ) : (
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{a.actor?.startsWith("staff:") ? "Staff" : "Assistant"}</span> {activityLabel(a).toLowerCase()}
                  <span className="text-slate-500"> · </span>
                  <Link href={customerHref(a.tenant_id, a.principal_id)} className="hover:underline">{a.principal_id}</Link>
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

export function HandoffsView() {
  const { data, error } = useLoad("handoffs", staffApi.handoffs);
  if (!data) return <><PageTitle title="Hand-offs" /><Loading error={error} /></>;
  return (
    <>
      <PageTitle title="Hand-offs" sub="Conversations the assistant passed to a person, by urgency and SLA" />
      {data.length === 0 ? (
        <Empty title="No hand-offs waiting">
          This queue fills when the assistant escalates a conversation to a person (hitl.queues). Nothing sends
          escalations here yet, so it stays empty; hand-offs in the chat raise tickets instead.
        </Empty>
      ) : (
        <div className={`${panel} overflow-x-auto`}>
          <table className="w-full min-w-[720px] text-sm">
            <thead className="border-b border-slate-200 dark:border-slate-800">
              <tr><th className={th}>Customer</th><th className={th}>Team</th><th className={th}>Reason</th><th className={th}>Urgency</th>
                <th className={th}>Status</th><th className={th}>SLA due</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.map((h) => (
                <tr key={h.id}>
                  <td className={td}>{h.customer_id}<span className="block text-xs text-slate-500">{h.tenant_id}</span></td>
                  <td className={td}>{queue(h.queue)}</td><td className={td}>{h.reason.replaceAll("_", " ")}</td>
                  <td className={`${td} tabular-nums`}>{h.acuity}</td><td className={td}>{h.status}</td><td className={td}>{when(h.sla_due_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
