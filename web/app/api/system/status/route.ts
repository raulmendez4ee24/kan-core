import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/backend";

type HealthShape = {
  ok?: boolean;
  status?: string;
};

export async function GET() {
  const base = apiBaseUrl();
  const token = cookies().get("kan_console_token")?.value || "";
  const clientId = cookies().get("kan_client_id")?.value || "";
  const clientToken = cookies().get("kan_client_token")?.value || "";

  let backendOk = false;
  let backendStatus = "down";
  let backendLatencyMs: number | null = null;
  const t0 = Date.now();
  try {
    const health = await fetch(`${base}/healthz`, { cache: "no-store" });
    backendLatencyMs = Date.now() - t0;
    if (health.ok) {
      const body = (await health.json().catch(() => ({}))) as HealthShape;
      backendOk = Boolean(body.ok ?? health.ok);
      backendStatus = String(body.status || "ok");
    }
  } catch {
    backendOk = false;
  }

  const hasConsoleToken = Boolean(token);
  const hasClientKeys = Boolean(clientId && clientToken);

  return NextResponse.json(
    {
      ok: backendOk,
      backend: {
        ok: backendOk,
        status: backendStatus,
        latency_ms: backendLatencyMs
      },
      auth: {
        console_token: hasConsoleToken,
        client_keys: hasClientKeys
      },
      now: new Date().toISOString()
    },
    { status: 200 }
  );
}

