// Incremental Server-Sent Events parser (WHATWG event-stream format): feed it text chunks as they arrive,
// get back the complete events. `id` is the last event id seen so far, as the spec defines it.
export type RawEvent = { id: string | null; event: string; data: string };

export function createParser() {
  let buffer = "";
  let data: string[] = [];
  let event = "message";
  let id: string | null = null;
  return function feed(chunk: string): RawEvent[] {
    buffer += chunk;
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    const events: RawEvent[] = [];
    for (const raw of lines) {
      const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
      if (line === "") {
        if (data.length) events.push({ id, event, data: data.join("\n") });
        data = [];
        event = "message";
        continue;
      }
      if (line.startsWith(":")) continue; // comment, e.g. a keep-alive
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") data.push(value);
      else if (field === "event") event = value;
      else if (field === "id") id = value;
    }
    return events;
  };
}
