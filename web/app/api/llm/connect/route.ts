import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/backend";

export async function POST(req: Request) {
  const form = await req.formData();
  const apiKey = String(form.get("api_key") || "").trim();
  const modelName = String(form.get("model_name") || "").trim();
  const token = cookies().get("kan_console_token")?.value || "";

  const redirectUrl = new URL("/settings", req.url);

  if (!token) {
    redirectUrl.searchParams.set("error", "No hay sesion activa.");
    return NextResponse.redirect(redirectUrl);
  }
  if (!apiKey) {
    redirectUrl.searchParams.set("error", "Falta API key.");
    return NextResponse.redirect(redirectUrl);
  }

  const res = await fetch(`${apiBaseUrl()}/console/llm/connect`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({
      provider: "openai",
      api_key: apiKey,
      model_name: modelName || null,
      metadata: { source: "web_settings" }
    }),
    cache: "no-store"
  });

  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    redirectUrl.searchParams.set(
      "error",
      String(body?.detail || body?.error || "No se pudo conectar OpenAI.")
    );
    return NextResponse.redirect(redirectUrl);
  }

  redirectUrl.searchParams.set("ok", "1");
  return NextResponse.redirect(redirectUrl);
}
