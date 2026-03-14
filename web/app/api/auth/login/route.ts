import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/backend";

export async function POST(req: Request) {
  const form = await req.formData();
  const email = String(form.get("email") || "").trim();
  const password = String(form.get("password") || "").trim();
  const organization_id = String(form.get("organization_id") || "").trim();

  const loginUrl = new URL("/login", req.url);
  if (!email || !password) {
    loginUrl.searchParams.set("error", "Completa email y password.");
    return NextResponse.redirect(loginUrl);
  }

  const res = await fetch(`${apiBaseUrl()}/console/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      password,
      organization_id: organization_id || null
    }),
    cache: "no-store"
  });

  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    loginUrl.searchParams.set(
      "error",
      String(body?.detail || body?.error || "No se pudo iniciar sesión.")
    );
    return NextResponse.redirect(loginUrl);
  }

  const token = String(body?.token || "");
  const expires_at = String(body?.expires_at || "");
  if (!token) {
    loginUrl.searchParams.set("error", "Respuesta inválida del backend (missing token).");
    return NextResponse.redirect(loginUrl);
  }

  const resp = NextResponse.redirect(new URL("/dashboard", req.url));
  resp.cookies.set("kan_console_token", token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/"
  });
  resp.cookies.set("kan_console_expires_at", expires_at, {
    httpOnly: true,
    sameSite: "lax",
    path: "/"
  });
  return resp;
}
