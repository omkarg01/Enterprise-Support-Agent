import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Shell } from "@/components/staff/ui.tsx";

// The demo staff console: its own shell and look, kept out of search engines. Staff sign in (Shell).
export const metadata: Metadata = {
  title: "Staff Console",
  description: "Demo support staff console: tickets, customers and their services.",
  robots: { index: false, follow: false },
};

export default function StaffLayout({ children }: { children: ReactNode }) {
  return <Shell>{children}</Shell>;
}
