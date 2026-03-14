import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/backend";

type ExecuteBody = {
  goal?: string;
  start_url?: string;
  execution_mode?: "safe" | "balanced" | "autonomous";
  max_steps?: number;
  step_timeout_seconds?: number;
  min_sources?: number;
  max_results_per_query?: number;
  quality_min_score?: number;
  enable_self_healing?: boolean;
  self_heal_max_attempts?: number;
  enforce_quality_gate?: boolean;
  dynamic_risk_controls?: boolean;
  enable_recovery?: boolean;
  stop_on_failure?: boolean;
  include_brave_fallback?: boolean;
  require_verified_sources?: boolean;
  allow_high_risk_auto_approval?: boolean;
  auto_learn?: boolean;
  client_id?: string;
  client_token?: string;
};

function pickClientCredentials(body: ExecuteBody) {
  const store = cookies();
  const cookieClientId = store.get("kan_client_id")?.value || "";
  const cookieClientToken = store.get("kan_client_token")?.value || "";
  const envClientId = String(process.env.KAN_CLIENT_ID || "").trim();
  const envClientToken = String(process.env.KAN_CLIENT_TOKEN || "").trim();
  const clientId = String(body.client_id || cookieClientId || envClientId).trim();
  const clientToken = String(body.client_token || cookieClientToken || envClientToken).trim();
  return { clientId, clientToken };
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as ExecuteBody;
  const goal = String(body.goal || "").trim();
  if (!goal) {
    return NextResponse.json({ error: "goal is required" }, { status: 400 });
  }

  const { clientId, clientToken } = pickClientCredentials(body);
  if (!clientId || !clientToken) {
    return NextResponse.json(
      { error: "missing client credentials (kan_client_id / kan_client_token)" },
      { status: 401 }
    );
  }

  const payload = {
    goal,
    start_url: String(body.start_url || "").trim() || null,
    execution_mode: body.execution_mode || "balanced",
    max_steps: Number(body.max_steps || 8),
    step_timeout_seconds: Number(body.step_timeout_seconds || 35),
    min_sources: Number(body.min_sources || 5),
    max_results_per_query: Number(body.max_results_per_query || 5),
    include_brave_fallback: body.include_brave_fallback ?? true,
    require_verified_sources: body.require_verified_sources ?? true,
    enable_recovery: body.enable_recovery ?? true,
    stop_on_failure: body.stop_on_failure ?? false,
    enable_self_healing: body.enable_self_healing ?? true,
    self_heal_max_attempts: Number(body.self_heal_max_attempts || 3),
    quality_min_score: Number(body.quality_min_score || 0.7),
    enforce_quality_gate: body.enforce_quality_gate ?? true,
    dynamic_risk_controls: body.dynamic_risk_controls ?? true,
    enable_learning: body.auto_learn ?? true,
    auto_optimize: true,
    dry_run: false,
    wait_for_results: true,
    context: {
      source: "web_autonomy_dashboard",
      allow_high_risk_auto_approval: Boolean(body.allow_high_risk_auto_approval)
    }
  };

  const headers = {
    "Content-Type": "application/json",
    "X-Client-Id": clientId,
    "X-Client-Token": clientToken
  };

  const runRes = await fetch(`${apiBaseUrl()}/autonomy/v2/run`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    cache: "no-store"
  });
  const runBody = await runRes.json().catch(() => ({}));
  if (!runRes.ok) {
    return NextResponse.json(
      { error: String(runBody?.detail || runBody?.error || "autonomy_run_failed"), raw: runBody },
      { status: runRes.status }
    );
  }

  return NextResponse.json({
    status: "ok",
    run: runBody
  });
}
