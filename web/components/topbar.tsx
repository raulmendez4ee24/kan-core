import Link from "next/link";
import { cookies } from "next/headers";

import { Badge } from "@/components/ui/badge";

export function Topbar() {
  const token = cookies().get("kan_console_token")?.value;
  const isAuthed = Boolean(token);

  return (
    <div
      className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"
      style={{
        padding: "14px 18px",
        borderRadius: 16,
        border: "1px solid rgba(15,23,42,.10)",
        background:
          "linear-gradient(120deg, rgba(255,255,255,.88), rgba(243,247,255,.86))",
        backdropFilter: "blur(8px)"
      }}
    >
      <div className="flex items-end gap-3" style={{ display: "flex", gap: 12, alignItems: "end" }}>
        <Link
          href="/"
          className="text-2xl font-semibold tracking-tight"
          style={{ fontSize: 40, lineHeight: 1, fontWeight: 700, letterSpacing: "-.03em", color: "#0f172a" }}
        >
          KAN Console
        </Link>
        <Badge className="bg-ink-100 text-ink-900 ring-ink-200">Task Studio</Badge>
      </div>

      <div className="flex items-center gap-3 text-sm" style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        <Link
          href="/dashboard"
          className="rounded-lg px-3 py-2 hover:bg-black/5"
          style={{ padding: "8px 12px", borderRadius: 10, color: "#111827", textDecoration: "none", fontWeight: 600 }}
        >
          Dashboard
        </Link>
        <Link href="/tasks" className="rounded-lg px-3 py-2 hover:bg-black/5" style={{ padding: "8px 12px", borderRadius: 10, color: "#111827", textDecoration: "none", fontWeight: 600 }}>
          Tasks
        </Link>
        <Link href="/autonomy" className="rounded-lg px-3 py-2 hover:bg-black/5" style={{ padding: "8px 12px", borderRadius: 10, color: "#111827", textDecoration: "none", fontWeight: 600 }}>
          Autonomy
        </Link>
        <Link href="/trial" className="rounded-lg px-3 py-2 hover:bg-black/5" style={{ padding: "8px 12px", borderRadius: 10, color: "#111827", textDecoration: "none", fontWeight: 600 }}>
          Trial
        </Link>
        <Link href="/settings" className="rounded-lg px-3 py-2 hover:bg-black/5" style={{ padding: "8px 12px", borderRadius: 10, color: "#111827", textDecoration: "none", fontWeight: 600 }}>
          Settings
        </Link>
        {isAuthed ? (
          <form action="/api/auth/logout" method="post">
            <button className="rounded-lg px-3 py-2 hover:bg-black/5" style={{ padding: "8px 12px", borderRadius: 10, color: "#111827", textDecoration: "none", fontWeight: 600 }}>
              Logout
            </button>
          </form>
        ) : (
          <Link href="/login" className="rounded-lg px-3 py-2 hover:bg-black/5" style={{ padding: "8px 12px", borderRadius: 10, color: "#111827", textDecoration: "none", fontWeight: 600 }}>
            Login
          </Link>
        )}
      </div>
    </div>
  );
}
