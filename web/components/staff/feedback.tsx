"use client";
// The staff console's Feedback page (built outside Genesis, 2026-11-20): the portal's thumbs on the assistant's
// replies, the thumbs-up rate by day and by model, and every thumbs down with its question, reply and comment,
// most severe first. Thumbs down become regression cases with `python -m evals.regressions export`.
import { useState } from "react";

import { CRITICAL, percent, staffApi, worstFirst, type Rate, type Rating } from "@/lib/staff.ts";
import { Empty, Loading, PageTitle, Select, panel, td, th, useLoad, when } from "./ui.tsx";

const failure = (c: string | null) => c ? c.replace(".", " · ").replaceAll("_", " ") : "no reason given";

function Tile({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className={`${panel} p-4`}>
      <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{sub}</p>}
    </div>
  );
}

function Rates({ title, first, rows }: { title: string; first: string; rows: [string, Rate][] }) {
  return (
    <section className={`${panel} overflow-x-auto`} aria-label={title}>
      <h2 className="px-3 pt-3 text-sm font-semibold">{title}</h2>
      <table className="w-full text-sm">
        <thead><tr><th className={th}>{first}</th><th className={th}>Up</th><th className={th}>Down</th><th className={th}>Up rate</th></tr></thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {rows.map(([key, r]) => (
            <tr key={key}><td className={td}>{key}</td><td className={`${td} tabular-nums`}>{r.up}</td>
              <td className={`${td} tabular-nums`}>{r.down}</td><td className={`${td} tabular-nums`}>{percent(r.up_rate)}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Down({ rating }: { rating: Rating }) {
  const critical = rating.severity !== null && rating.severity >= CRITICAL;
  return (
    <li className={`${panel} p-4 ${critical ? "border-rose-300 dark:border-rose-800" : ""}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
        <span className={`rounded px-1.5 py-0.5 font-medium ${critical
          ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300"
          : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"}`}>
          {critical && "Critical · "}{failure(rating.category)}
        </span>
        {rating.severity !== null && <span>severity {rating.severity.toFixed(2)}</span>}
        <span>{rating.models.join(", ") || "model unknown"}</span>
        <span>{rating.tenant_id}</span>
        <span className="ml-auto">{when(rating.at)}</span>
      </div>
      <p className="mt-3 text-sm"><span className="font-medium">Question: </span>{rating.question ?? "(not kept)"}</p>
      <details className="mt-1 text-sm">
        <summary className="cursor-pointer text-slate-600 dark:text-slate-300">Answer</summary>
        <p className="mt-1 whitespace-pre-wrap text-slate-700 dark:text-slate-300">{rating.answer ?? "(not kept)"}</p>
      </details>
      <p className="mt-2 text-sm"><span className="font-medium">Comment: </span>{rating.comment || "(none)"}</p>
      <p className="mt-2 break-all text-xs text-slate-400">conversation {rating.conversation_id} · turn {rating.turn_id}</p>
    </li>
  );
}

export function FeedbackView() {
  const [days, setDays] = useState("30");
  const { data, error } = useLoad(`feedback-${days}`, () => staffApi.feedback(Number(days)));
  const range = <Select label="Period" value={days} onChange={setDays}
    options={[["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"]]} />;
  if (!data) return <><PageTitle title="Feedback">{range}</PageTitle><Loading error={error} /></>;
  const { total, by_day, by_model, down_by_category } = data.summary;
  const downs = worstFirst(data.ratings);
  const critical = downs.filter((r) => r.severity !== null && r.severity >= CRITICAL).length;
  return (
    <>
      <PageTitle title="Feedback" sub="Thumbs on the assistant's replies in the customer portal">{range}</PageTitle>
      {data.ratings.length === 0 ? (
        <Empty title="No ratings in this period">Customers rate a reply with the thumbs under it in the portal.</Empty>
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile label="Thumbs-up rate" value={percent(total.up_rate)} sub={`${total.up} up of ${total.up + total.down}`} />
            <Tile label="Ratings" value={total.up + total.down} />
            <Tile label="Thumbs down" value={total.down} />
            <Tile label="Critical" value={critical} sub={`severity ${CRITICAL} or more`} />
          </div>
          <div className="grid gap-6 lg:grid-cols-3">
            <Rates title="By model" first="Model" rows={Object.entries(by_model)} />
            <Rates title="By day" first="Day" rows={Object.entries(by_day).reverse()} />
            <section className={`${panel} p-4`} aria-label="Thumbs down by failure">
              <h2 className="mb-2 text-sm font-semibold">Thumbs down by failure</h2>
              <ul className="space-y-1.5 text-sm">
                {Object.entries(down_by_category).map(([c, n]) => (
                  <li key={c} className="flex justify-between gap-3"><span>{failure(c === "no reason given" ? null : c)}</span>
                    <span className="tabular-nums text-slate-600 dark:text-slate-300">{n}</span></li>
                ))}
              </ul>
            </section>
          </div>
          <section aria-label="Thumbs down">
            <h2 className="mb-3 text-sm font-semibold">Thumbs down ({downs.length}), most severe first</h2>
            <ul className="space-y-3">{downs.map((r) => <Down key={r.id} rating={r} />)}</ul>
          </section>
        </div>
      )}
    </>
  );
}
