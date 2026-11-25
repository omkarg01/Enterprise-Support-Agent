"use client";
// Staff services: every customer's servers and container apps with their live status, and the actions staff
// may carry out on them (tools.services.staff_act: the same rules and 8-second settle as a customer's offer).
import Link from "next/link";
import { useMemo, useState } from "react";

import {
  NO_SERVICE_FILTER, actionsFor, customerHref, filterServices, optionsOf, staffApi, type ServiceFilter,
  type StaffService,
} from "@/lib/staff.ts";
import { kindLabel, statusLabel } from "@/lib/services.ts";
import { Button, Empty, Loading, PageTitle, Problem, Search, Select, ServiceState, input, panel, td, th, useLoad } from "./ui.tsx";

const POLL_MS = 30_000;  // only while the tab is visible (useLoad)
const LABELS = { stop: "Stop", start: "Start", restart: "Restart", scale: "Scale" } as const;

function Actions({ service, onDone }: { service: StaffService; onDone: (problem: string | null) => void }) {
  const [replicas, setReplicas] = useState(String(service.replicas ?? 1));
  const [busy, setBusy] = useState(false);
  const actions = actionsFor(service);
  if (actions.length === 0) return <span className="text-xs text-slate-500">Settling…</span>;
  async function act(action: keyof typeof LABELS) {
    setBusy(true);
    try {
      await staffApi.act(service.tenant_id, service.service_id, action, action === "scale" ? Number(replicas) : null);
      onDone(null);
    } catch (e) {
      onDone(`${LABELS[action]} ${service.name}: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {actions.map((action) => action === "scale" ? (
        <span key={action} className="flex items-center gap-1">
          <input type="number" min={1} max={10} value={replicas} onChange={(e) => setReplicas(e.target.value)}
            aria-label={`Replicas for ${service.name}`} className={`${input} w-16 py-1`} />
          <Button disabled={busy || !(Number(replicas) >= 1 && Number(replicas) <= 10)} onClick={() => act("scale")}>Scale</Button>
        </span>
      ) : (
        <Button key={action} tone={action === "stop" ? "danger" : "plain"} disabled={busy} onClick={() => act(action)}>
          {LABELS[action]}
        </Button>
      ))}
    </div>
  );
}

export function ServicesTable({ services, onChanged, showCustomer = true }: {
  services: StaffService[]; onChanged: () => void; showCustomer?: boolean;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <>
      {problem && <div className="mb-3"><Problem>{problem}</Problem></div>}
      <div className={`${panel} overflow-x-auto`}>
        <table className="w-full min-w-[720px] text-sm">
          <thead className="border-b border-slate-200 dark:border-slate-800">
            <tr>
              <th className={th}>Service</th>{showCustomer && <th className={th}>Customer</th>}
              <th className={th}>Type</th><th className={th}>Region</th><th className={th}>Replicas</th>
              <th className={th}>Status</th><th className={th}>Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {services.map((s) => (
              <tr key={`${s.tenant_id}/${s.service_id}`}>
                <td className={td}><span className="font-mono font-medium">{s.name}</span><span className="block text-xs text-slate-500">{s.service_id}</span></td>
                {showCustomer && (
                  <td className={td}>
                    <Link href={customerHref(s.tenant_id, s.principal_id)} className="hover:underline">{s.principal_id}</Link>
                    <span className="block text-xs text-slate-500">{s.tenant_id}</span>
                  </td>
                )}
                <td className={td}>{kindLabel(s.kind)}</td>
                <td className={td}>{s.region}</td>
                <td className={`${td} tabular-nums`}>{s.replicas ?? "—"}</td>
                <td className={td}><ServiceState status={s.status} /></td>
                <td className={td}><Actions key={`${s.status}/${s.replicas}`} service={s} onDone={(p) => { setProblem(p); onChanged(); }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function ServicesView() {
  const { data, error, reload } = useLoad("services", staffApi.services, POLL_MS);
  const [filter, setFilter] = useState<ServiceFilter>(NO_SERVICE_FILTER);
  const shown = useMemo(() => filterServices(data ?? [], filter), [data, filter]);
  const set = (patch: Partial<ServiceFilter>) => setFilter((f) => ({ ...f, ...patch }));
  const opts = (label: string, values: string[], show: (v: string) => string = (v) => v): [string, string][] =>
    [["all", label], ...values.map((v): [string, string] => [v, show(v)])];

  if (!data) return <><PageTitle title="Services" /><Loading error={error} /></>;
  return (
    <>
      <PageTitle title="Services" sub={`${shown.length} of ${data.length} servers and apps · live, every 10 s while this tab is open`}>
        <Button onClick={reload}>Refresh</Button>
      </PageTitle>
      <div className={`${panel} mb-4 flex flex-wrap items-end gap-3 p-3`}>
        <Search value={filter.q} onChange={(q) => set({ q })} placeholder="Name, customer or id" />
        <Select label="Type" value={filter.kind} onChange={(kind) => set({ kind })} options={opts("All types", ["server", "app"], kindLabel)} />
        <Select label="Status" value={filter.status} onChange={(status) => set({ status })}
          options={opts("All statuses", optionsOf(data, (s) => s.status), statusLabel)} />
        <Select label="Region" value={filter.region} onChange={(region) => set({ region })} options={opts("All regions", optionsOf(data, (s) => s.region))} />
        <Select label="Tenant" value={filter.tenant} onChange={(tenant) => set({ tenant })} options={opts("All tenants", optionsOf(data, (s) => s.tenant_id))} />
        <Button onClick={() => setFilter(NO_SERVICE_FILTER)}>Clear</Button>
      </div>
      {error && <div className="mb-3"><Problem>Refresh failed: {error}</Problem></div>}
      {shown.length === 0 ? <Empty title="No services match" /> : <ServicesTable services={shown} onChanged={reload} />}
    </>
  );
}
