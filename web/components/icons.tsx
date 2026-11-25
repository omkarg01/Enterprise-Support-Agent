// Stroke icons (24px grid), drawn inline so the page needs no icon font or extra request.
const paths = {
  chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.6A8 8 0 1 1 21 12Z",
  server: "M4 4h16v6H4z M4 14h16v6H4z M8 7h.01 M8 17h.01",
  box: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5 M12 12v9",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8 M3 3v5h5 M12 7v5l3 2",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4 M12 17h.01",
  external: "M14 4h6v6 M20 4l-9 9 M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z M4 21a8 8 0 0 1 16 0",
  mail: "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z M3 7l9 6 9-6",
  building: "M4 21V5l8-3v19 M12 8h8v13 M2 21h20 M7 8h2 M7 12h2 M7 16h2 M15 12h2 M15 16h2",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12 M18 6L6 18",
  warn: "M12 3l10 18H2L12 3Z M12 10v4 M12 17.5h.01",
  send: "M4 12l16-8-6 17-3-7-7-2Z M11 14l9-10",
  plus: "M12 5v14 M5 12h14",
  doc: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z M14 3v5h5 M9 13h6 M9 17h6",
  chevron: "M6 9l6 6 6-6",
  right: "M9 6l6 6-6 6",
  tree: "M12 3v4 M6 13v-2h12v2 M12 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z M6 17a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z M12 17a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z M18 17a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z M12 13v-2",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15Z",
  copy: "M9 9h10v10H9z M5 15V5h10",
  logout: "M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4 M10 17l5-5-5-5 M15 12H4",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3Z M8.5 12l2.5 2.5 4.5-4.5",
  sources: "M4 5h7a2 2 0 0 1 2 2v12a2 2 0 0 0-2-2H4V5Z M20 5h-7a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2h7V5Z",
  ticket: "M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4V7Z M14 5v14",
  person: "M16 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M8 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M2 20a6 6 0 0 1 12 0 M14 20a6 6 0 0 1 8-5.6",
  thumbUp: "M7 10v12 M15 5.9L14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 8a2 2 0 0 1-1.9 1.4H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.8a2 2 0 0 0 1.8-1.1L12 2a3.1 3.1 0 0 1 3 3.9Z",
  thumbDown: "M17 14V2 M9 18.1L10 14H4.2a2 2 0 0 1-1.9-2.6l2.3-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.8a2 2 0 0 0-1.8 1.1L12 22a3.1 3.1 0 0 1-3-3.9Z",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, className = "size-5" }: { name: IconName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round"
      strokeLinejoin="round" className={className} aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}

export function Logo({ className = "size-9" }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={className} aria-hidden="true">
      <path d="M20 3l14.7 8.5v17L20 37 5.3 28.5v-17L20 3Z" fill="none" stroke="currentColor" strokeWidth="3" />
      <path d="M20 12l7 4v8l-7 4-7-4v-8l7-4Z" fill="currentColor" opacity=".9" />
    </svg>
  );
}
