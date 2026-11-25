// Chat state from typed events (UA-D7). The reply is buffered server-side until it passes the checks (UA-D8):
// `status` events show progress meanwhile, `final` carries the checked reply. Pure: the caller passes `at`.
import type { StreamEvent } from "./chat-client.ts";

export type Citation = { doc_id: string; title?: string; heading?: string; excerpt?: string };
export type Action = { id: string; label: string };
export type Message = {
  role: "user" | "assistant" | "system" | "card";
  text: string;
  at?: number;
  turnId?: string;
  citations?: Citation[];
  actions?: Action[];
  card?: Record<string, any>;
};
export type ChatState = {
  status: string | null;
  draft: string;
  citations: Citation[];
  messages: Message[];
  error: string | null;
};

export const initialState = (): ChatState => ({ status: null, draft: "", citations: [], messages: [], error: null });

export function addUserMessage(state: ChatState, text: string, at?: number): ChatState {
  return { ...state, error: null, messages: [...state.messages, { role: "user", text, at }] };
}

export function reduce(state: ChatState, { event, data, at }: Pick<StreamEvent, "event" | "data"> & { at?: number }):
  ChatState {
  switch (event) {
    case "status":
      // The conversation so far follows (a reopened chat, or a reconnect past the replay window).
      if (data.code === "stream_expired") return initialState();
      return { ...state, status: data.text ?? data.state ?? "Working on it" };
    case "text_delta":
      return { ...state, draft: state.draft + (data.text ?? "") };
    case "citation":
      return { ...state, citations: [...state.citations, data as Citation] };
    case "final": {
      const reply: Message = {
        role: data.role ?? "assistant",
        text: data.text ?? data.content ?? "",
        at: data.history && data.at ? Date.parse(data.at) : at, // a reopened chat keeps its own times
        turnId: data.turn_id, // a reopened reply keeps its turn (its trace) and its sources
        citations: data.history ? data.citations ?? [] : state.citations,
        actions: data.actions ?? [],
      };
      // The server sends a turn's card before its final event (so a retried turn republishes nothing); show the
      // reply first and the card it talks about after it.
      const cards = [];
      let kept = state.messages.length;
      while (kept > 0 && state.messages[kept - 1].role === "card" && data.turn_id
        && state.messages[kept - 1].card?.turn_id === data.turn_id) kept -= 1;
      cards.push(...state.messages.slice(kept));
      return { ...state, status: null, draft: "", citations: [],
        messages: [...state.messages.slice(0, kept), reply, ...cards] };
    }
    case "action_card":
      return { ...state, messages: [...state.messages, { role: "card", text: data.title ?? "Action", card: data, at }] };
    case "handoff":
      return {
        ...state,
        status: null,
        messages: [...state.messages, { role: "system", text: data.text ?? "A specialist will continue here.", at }],
      };
    case "error":
      return { ...state, status: null, error: data.text ?? "Something went wrong. Please try again." };
    default:
      return state;
  }
}
