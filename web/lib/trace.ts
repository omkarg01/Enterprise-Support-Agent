// Shapes of GET .../decisions (src/safety/governance/records.py why()) and GET .../turns/{id}/trace
// (src/channels/api/trace.py customer_trace()). The server has already filtered both for the caller's tier.
export type Why = {
  decision: string;
  reason: string;
  checks: string[];
  requires_human_review: boolean;
  human_available: boolean;
};

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
  agents: Agent[];
  shared_state: Record<string, string | number | null>;
};
