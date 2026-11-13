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
      if (data.code === "stream_expired") {
        return { ...initialState(), status: "Reconnected, showing the conversation so far" };
      }
      return { ...state, status: data.text ?? data.state ?? "Working on it" };
    case "text_delta":
      return { ...state, draft: state.draft + (data.text ?? "") };
    case "citation":
      return { ...state, citations: [...state.citations, data as Citation] };
    case "final":
      return {
        ...state,
        status: null,
        draft: "",
        citations: [],
        messages: [...state.messages, {
          role: data.role ?? "assistant",
          text: data.text ?? data.content ?? "",
          at,
          turnId: data.history ? undefined : data.turn_id,
          citations: state.citations,
          actions: data.actions ?? [],
        }],
      };
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
