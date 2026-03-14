import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/backend";

type Query = {
  limit?: string;
  order_by?: string;
  descending?: string;
};

function pickCredentials() {
  const store = cookies();
  const cookieClientId = store.get("kan_client_id")?.value || "";
  const cookieClientToken = store.get("kan_client_token")?.value || "";
  const envClientId = String(process.env.KAN_CLIENT_ID || "").trim();
  const envClientToken = String(process.env.KAN_CLIENT_TOKEN || "").trim();
  const clientId = cookieClientId || envClientId;
  const clientToken = cookieClientToken || envClientToken;
  return { clientId, clientToken };
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const query = Object.fromEntries(url.searchParams.entries()) as Query & {
    client_id?: string;
    client_token?: string;
  };
  const fromCookies = pickCredentials();
  const clientId = String(query.client_id || fromCookies.clientId || "").trim();
  const clientToken = String(query.client_token || fromCookies.clientToken || "").trim();
  if (!clientId || !clientToken) {
    return NextResponse.json(
      { error: "missing client credentials (kan_client_id / kan_client_token)" },
      { status: 401 }
    );
  }

  const qs = new URLSearchParams({
    limit: String(query.limit || "25"),
    order_by: String(query.order_by || "reliability"),
    descending: String(query.descending || "true")
  });

  const res = await fetch(`${apiBaseUrl()}/autonomy/v2/benchmarks?${qs.toString()}`, {
    method: "GET",
    headers: {
      "X-Client-Id": clientId,
      "X-Client-Token": clientToken
    },
    cache: "no-store"
  });
  const body = await res.json().catch(() => ({}));
  return NextResponse.json(body, { status: res.status });
}
