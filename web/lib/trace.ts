// Shape of GET .../turns/{id}/trace (src/channels/api/trace.py customer_trace()). The server has already
// filtered it for the caller's tier.

export type ToolCall = { name: string; args: Record<string, string>; result: string; items: string[] };

export type Agent = {
  id: string;
  name: string;
  role: string;
  status: "success" | "failed" | "stopped";
  outcome: string;
  calls: ToolCall[];
  notes: string[];
};

export type Trace = {
  turn_id: string;
  detail: "summary" | "full";
  tier: string;
  summary: string;
  final: { label: string; outcome: string };
  models?: string[]; // the language models that wrote the reply; absent from an older API
  agents: Agent[];
  shared_state: Record<string, string | number | null>;
};
