import { cookies } from "next/headers";

export function apiBaseUrl() {
  const raw = process.env.KAN_API_BASE_URL || "http://127.0.0.1:8000";
  return raw.replace(/\/+$/, "");
}

export function consoleTokenFromCookies() {
  const store = cookies();
  const token = store.get("kan_console_token")?.value || "";
  return token;
}

export async function consoleFetch(path: string, init?: RequestInit) {
  const token = consoleTokenFromCookies();
  const url = `${apiBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = new Headers(init?.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", headers.get("Content-Type") || "application/json");
  const res = await fetch(url, {
    ...init,
    headers,
    cache: "no-store"
  });
  return res;
}
