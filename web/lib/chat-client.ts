// API client for the portal: sign-in, idempotent turn submission, the resumable event stream, and the
// customer's why view and execution trace. The stream is read with fetch (not EventSource) because it must
// carry the Bearer token.
import { createParser } from "./sse.ts";
import type { Trace, Why } from "./trace.ts";

export class HttpError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`);
    this.status = status;
  }
}

export type StreamEvent = { id: string | null; event: string; data: Record<string, any> };
export type Tier = "T0_ANON" | "T1_VERIFIED" | "T2_SSO";
export type Claims = { sub: string; tid: string; tier: Tier; exp: number };

type Options = {
  baseUrl?: string;
  fetch?: typeof fetch;
  uuid?: () => string;
  sleep?: (ms: number) => Promise<void>;
};

// Display only: the server checks the signature and enforces the tier on every request.
export function readClaims(token: string | null): Claims | null {
  try {
    const body = token!.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(body));
  } catch {
    return null;
  }
}

export class ChatClient {
  baseUrl: string;
  fetch: typeof fetch;
  uuid: () => string;
  sleep: (ms: number) => Promise<void>;
  token: string | null = null;
  lastEventId: string | null = null;

  constructor({
    baseUrl = "",
    fetch = globalThis.fetch.bind(globalThis),
    uuid = () => globalThis.crypto.randomUUID(),
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  }: Options = {}) {
    this.baseUrl = baseUrl;
    this.fetch = fetch;
    this.uuid = uuid;
    this.sleep = sleep;
  }

  async signInAnonymous(tenantId: string) {
    this.token = (await this.#request("POST", "/v1/identity/anonymous", { tenant_id: tenantId })).access_token;
    return this.token;
  }

  async startOtp(tenantId: string, userId: string): Promise<string> {
    return (await this.#request("POST", "/v1/identity/otp/start", { tenant_id: tenantId, user_id: userId }))
      .challenge_id;
  }

  // Carries the current token, so the server steps this session up instead of starting a new one.
  async verifyOtp(tenantId: string, challengeId: string, code: string) {
    this.token = (await this.#request("POST", "/v1/identity/otp/verify",
      { tenant_id: tenantId, challenge_id: challengeId, code })).access_token;
    return this.token;
  }

  async logout() {
    await this.#request("POST", "/v1/identity/logout");
    this.token = null;
  }

  // One Idempotency-Key for every retry of the same message, so the server creates one turn.
  async send(conversationId: string, content: string, { attempts = 3 } = {}) {
    const key = this.uuid();
    for (let attempt = 1; ; attempt += 1) {
      try {
        return await this.#request("POST", `${conversation(conversationId)}/turns`, { content },
          { "Idempotency-Key": key });
      } catch (error) {
        const retryable = !(error instanceof HttpError) || error.status >= 500;
        if (!retryable || attempt >= attempts) throw error;
        await this.sleep(250 * 2 ** attempt);
      }
    }
  }

  why(conversationId: string, turnId: string): Promise<Why[]> {
    return this.#request("GET", `${conversation(conversationId)}/decisions?turn_id=${encodeURIComponent(turnId)}`);
  }

  trace(conversationId: string, turnId: string): Promise<Trace> {
    return this.#request("GET", `${conversation(conversationId)}/turns/${encodeURIComponent(turnId)}/trace`);
  }

  // Calls onEvent for every event; when the stream drops it reconnects with Last-Event-ID so missed events
  // are replayed. Stops when `signal` aborts or the server refuses (4xx).
  async listen(conversationId: string, onEvent: (event: StreamEvent) => void,
    { signal, retryMs = 1000 }: { signal?: AbortSignal; retryMs?: number } = {}) {
    const url = `${this.baseUrl}${conversation(conversationId)}/events`;
    while (!signal?.aborted) {
      try {
        const headers: Record<string, string> = { ...this.#auth() };
        if (this.lastEventId) headers["Last-Event-ID"] = this.lastEventId;
        const reply = await this.fetch(url, { headers, signal });
        if (!reply.ok) throw new HttpError(reply.status, await reply.text());
        const feed = createParser();
        const decoder = new TextDecoder();
        const reader = reply.body!.getReader();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          for (const event of feed(decoder.decode(value, { stream: true }))) {
            if (event.id) this.lastEventId = event.id;
            onEvent({ ...event, data: JSON.parse(event.data) });
          }
        }
      } catch (error) {
        if (signal?.aborted) return;
        if (error instanceof HttpError && error.status < 500) throw error;
      }
      await this.sleep(retryMs);
    }
  }

  #auth(): Record<string, string> {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  async #request(method: string, path: string, body?: unknown, headers: Record<string, string> = {}) {
    const reply = await this.fetch(`${this.baseUrl}${path}`, {
      method,
      headers: { ...(body === undefined ? {} : { "Content-Type": "application/json" }), ...this.#auth(), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!reply.ok) throw new HttpError(reply.status, await reply.text());
    return reply.status === 204 ? null : reply.json();
  }
}

const conversation = (id: string) => `/v1/conversations/${encodeURIComponent(id)}`;
