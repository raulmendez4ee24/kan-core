import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/backend";

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const message = String(body?.message || "").trim();
  const session_id = String(body?.session_id || "trial-demo").trim() || "trial-demo";

  if (!message) {
    return NextResponse.json({ error: "message required" }, { status: 400 });
  }

  const clientId = cookies().get("kan_client_id")?.value || "";
  const clientToken = cookies().get("kan_client_token")?.value || "";
  if (!clientId || !clientToken) {
    return NextResponse.json({ error: "missing client credentials" }, { status: 401 });
  }

  const res = await fetch(`${apiBaseUrl()}/chat/message`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": clientId,
      "X-Client-Token": clientToken
    },
    body: JSON.stringify({ session_id, message, channel: "web" }),
    cache: "no-store"
  });

  const data = await res.json().catch(() => ({}));
  return NextResponse.json(data, { status: res.status });
}

