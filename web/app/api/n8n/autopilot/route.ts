import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/backend";

type Body = {
  request_text?: string;
  industry?: string;
  desired_functions?: string[];
  provider_allowlist?: string[];
};

const CANONICAL_FUNCTIONS = new Set([
  "crm",
  "calendar",
  "payments",
  "helpdesk",
  "shopify",
  "inventory",
  "meta",
  "email",
  "messaging"
]);

const KEYWORD_RULES: Array<{ fn: string; terms: string[] }> = [
  { fn: "crm", terms: ["crm", "lead", "leads", "ventas", "sales", "pipeline", "prospecto", "prospectos"] },
  { fn: "calendar", terms: ["calendario", "calendar", "cita", "citas", "agenda", "agendar", "appointment", "schedule"] },
  { fn: "payments", terms: ["pago", "pagos", "cobro", "cobros", "checkout", "billing", "payment", "payments"] },
  { fn: "helpdesk", terms: ["soporte", "support", "ticket", "tickets", "helpdesk"] },
  { fn: "shopify", terms: ["shopify", "tienda", "ecommerce", "orden", "ordenes", "pedido", "pedidos"] },
  { fn: "inventory", terms: ["inventario", "inventory", "stock", "sku"] },
  { fn: "meta", terms: ["meta", "whatsapp", "facebook", "instagram", "messenger"] },
  { fn: "email", terms: ["email", "correo", "mail", "newsletter", "smtp"] },
  { fn: "messaging", terms: ["mensajeria", "messaging", "sms", "twilio", "notificacion", "notificaciones"] }
];

function normalizeText(value: string) {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
}

function inferDesiredFunctions(text: string, explicit: string[]) {
  const selected: string[] = [];
  const seen = new Set<string>();

  for (const item of explicit) {
    const token = String(item || "").trim().toLowerCase();
    if (CANONICAL_FUNCTIONS.has(token) && !seen.has(token)) {
      seen.add(token);
      selected.push(token);
    }
  }
  if (selected.length) return selected;

  const normalized = normalizeText(text);
  for (const rule of KEYWORD_RULES) {
    if (seen.has(rule.fn)) continue;
    if (rule.terms.some((term) => normalized.includes(term))) {
      seen.add(rule.fn);
      selected.push(rule.fn);
    }
  }

  // Hard fallback to avoid "No se detectaron funciones requeridas" on generic prompts.
  if (!selected.length) {
    return ["crm", "messaging", "meta"];
  }
  return selected;
}

function resolveClientCredentials() {
  const store = cookies();
  const cookieId = store.get("kan_client_id")?.value || "";
  const cookieToken = store.get("kan_client_token")?.value || "";
  const envId = String(process.env.KAN_CLIENT_ID || "").trim();
  const envToken = String(process.env.KAN_CLIENT_TOKEN || "").trim();
  return {
    clientId: String(cookieId || envId).trim(),
    clientToken: String(cookieToken || envToken).trim()
  };
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as Body;
  const requestText = String(body.request_text || "").trim();
  if (!requestText) {
    return NextResponse.json({ error: "request_text is required" }, { status: 400 });
  }

  const { clientId, clientToken } = resolveClientCredentials();
  if (!clientId || !clientToken) {
    return NextResponse.json(
      { error: "missing client credentials (KAN_CLIENT_ID / KAN_CLIENT_TOKEN)" },
      { status: 401 }
    );
  }

  const payload = {
    request_text: requestText,
    industry: String(body.industry || "").trim() || null,
    desired_functions: inferDesiredFunctions(
      requestText,
      Array.isArray(body.desired_functions) ? body.desired_functions : []
    ),
    allow_auto_registration: true,
    auto_generate_workflow: true,
    enable_provider_fallback: true,
    auto_repair_max_attempts: 2,
    continue_on_error: true,
    dry_run: false,
    supplied_api_keys: {},
    provider_allowlist: Array.isArray(body.provider_allowlist) ? body.provider_allowlist : [],
    provider_blocklist: [],
    max_candidates_per_function: 3,
    test_timeout_seconds: 6
  };

  const res = await fetch(`${apiBaseUrl()}/integrations/autopilot`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Client-Id": clientId,
      "X-Client-Token": clientToken
    },
    body: JSON.stringify(payload),
    cache: "no-store"
  });
  const raw = await res.json().catch(() => ({}));
  if (!res.ok) {
    return NextResponse.json(
      { error: String((raw as { detail?: string; error?: string }).detail || (raw as { error?: string }).error || "autopilot_failed"), raw },
      { status: res.status }
    );
  }

  return NextResponse.json(raw);
}
