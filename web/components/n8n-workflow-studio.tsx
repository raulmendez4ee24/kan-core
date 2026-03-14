"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type ResultItem = {
  function_name: string;
  provider_id?: string | null;
  integration_name?: string | null;
  status: string;
  reason: string;
  workflowId?: string | number | null;
  url?: string | null;
  next_actions?: string[];
};

type AutoPilotResponse = {
  detected_functions: string[];
  results: ResultItem[];
};

type PresetPlan = {
  id: string;
  name: string;
  subtitle: string;
  request_text: string;
  desired_functions: string[];
};

const PLAN_PRESETS: PresetPlan[] = [
  {
    id: "starter",
    name: "Starter",
    subtitle: "WhatsApp/webchat + agenda + 3 automatizaciones + panel basico",
    request_text:
      "Plan Starter: Bot WhatsApp/webchat + agenda y recordatorios + 3 automatizaciones + panel basico de resultados",
    desired_functions: ["meta", "messaging", "calendar", "crm"]
  },
  {
    id: "growth",
    name: "Growth",
    subtitle: "Sistema operativo PyME con mas automatizaciones",
    request_text:
      "Plan Growth: Todo Starter + 5 a 15 automatizaciones en n8n + 2 a 5 integraciones + reporte semanal y monitoreo",
    desired_functions: ["meta", "messaging", "calendar", "crm", "email", "helpdesk"]
  },
  {
    id: "commerce",
    name: "Commerce",
    subtitle: "Atencion + pedidos + postventa + recuperacion carrito",
    request_text:
      "Plan Commerce: Ecommerce autopilot con atencion y pedidos, recuperacion de carrito, reportes de ventas e integracion de tienda",
    desired_functions: ["shopify", "crm", "messaging", "email", "payments"]
  },
  {
    id: "enterprise",
    name: "Enterprise",
    subtitle: "Operacion total, auditoria, SLA y monitoreo",
    request_text:
      "Plan Enterprise: Auditoria completa de procesos, automatizacion total, agentes y monitoreo continuo con SLA",
    desired_functions: ["crm", "helpdesk", "calendar", "messaging", "email", "payments", "inventory"]
  }
];

export function N8NWorkflowStudio() {
  const [prompt, setPrompt] = React.useState("");
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState("");
  const [data, setData] = React.useState<AutoPilotResponse | null>(null);
  const [selectedPreset, setSelectedPreset] = React.useState<string>("");

  async function createFlow(options?: { requestText?: string; desiredFunctions?: string[] }) {
    const requestText = String(options?.requestText || prompt).trim();
    if (!requestText || running) return;
    setRunning(true);
    setError("");
    setData(null);
    try {
      const res = await fetch("/api/n8n/autopilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_text: requestText,
          desired_functions: options?.desiredFunctions || []
        })
      });
      const body = (await res.json().catch(() => ({}))) as AutoPilotResponse & { error?: string };
      if (!res.ok) throw new Error(String(body.error || "No se pudo crear el flujo"));
      setData(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error desconocido");
    } finally {
      setRunning(false);
    }
  }

  return (
    <Card
      style={{
        borderRadius: 20,
        padding: 24,
        background: "linear-gradient(155deg, #0b1220 0%, #17233b 52%, #1f2f4f 100%)",
        color: "#f8fafc",
        border: "1px solid rgba(148,163,184,.28)"
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start", flexWrap: "wrap" }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 42, letterSpacing: "-0.03em", lineHeight: 1.05, fontWeight: 700 }}>
            Localhost directo. 1 click y corre.
          </h1>
          <p style={{ marginTop: 10, color: "rgba(226,232,240,.82)", fontSize: 15 }}>
            Elige plan o escribe una instruccion. El bot arma y crea workflows en n8n.
          </p>
        </div>
        <div style={{ fontSize: 12, padding: "8px 10px", borderRadius: 999, border: "1px solid rgba(148,163,184,.45)", color: "#cbd5e1" }}>
          Modo operativo
        </div>
      </div>

      <div style={{ marginTop: 16, display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))" }}>
        {PLAN_PRESETS.map((preset) => {
          const active = selectedPreset === preset.id;
          return (
            <button
              key={preset.id}
              type="button"
              onClick={() => {
                setSelectedPreset(preset.id);
                setPrompt(preset.request_text);
              }}
              style={{
                textAlign: "left",
                borderRadius: 14,
                padding: "12px 14px",
                border: active ? "1px solid rgba(34,211,238,.8)" : "1px solid rgba(148,163,184,.32)",
                background: active ? "rgba(8,47,73,.55)" : "rgba(15,23,42,.45)",
                color: "#e2e8f0",
                cursor: "pointer"
              }}
            >
              <div style={{ fontWeight: 700, fontSize: 15 }}>{preset.name}</div>
              <div style={{ marginTop: 4, fontSize: 13, color: "rgba(203,213,225,.86)" }}>{preset.subtitle}</div>
            </button>
          );
        })}
      </div>

      <div style={{ marginTop: 18, display: "grid", gap: 12 }}>
        <textarea
          rows={6}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ej: Cliente dental quiere: capturar leads de WhatsApp, calificarlos con IA, crear contacto en CRM y mandar seguimiento automatico."
          style={{
            width: "100%",
            borderRadius: 14,
            border: "1px solid rgba(148,163,184,.4)",
            background: "rgba(15,23,42,.64)",
            color: "#f8fafc",
            padding: "14px",
            fontSize: 15,
            outline: "none"
          }}
        />
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Button
            onClick={() => {
              const preset = PLAN_PRESETS.find((p) => p.id === selectedPreset);
              void createFlow({
                requestText: prompt,
                desiredFunctions: preset?.desired_functions || []
              });
            }}
            disabled={running || !prompt.trim()}
            style={{ background: "#f8fafc", color: "#0f172a", fontWeight: 700, borderRadius: 10 }}
          >
            {running ? "Ejecutando..." : "Ejecutar ahora"}
          </Button>
          <Button
            onClick={() =>
              void createFlow({
                requestText: "Busca en internet lo mas vendido en Amazon y crea workflow de monitoreo diario con salida a reporte",
                desiredFunctions: ["messaging", "crm", "email"]
              })
            }
            disabled={running}
            style={{ background: "transparent", color: "#c7d2fe", border: "1px solid rgba(129,140,248,.55)", fontWeight: 600, borderRadius: 10 }}
          >
            Demo: Amazon top sellers
          </Button>
          <span style={{ color: "rgba(226,232,240,.72)", fontSize: 13 }}>
            Usa `KAN_CLIENT_*` y `N8N_*`. Si falta una API, te dira cual.
          </span>
        </div>
      </div>

      {error ? (
        <div
          style={{
            marginTop: 14,
            borderRadius: 12,
            border: "1px solid rgba(248,113,113,.45)",
            background: "rgba(220,38,38,.16)",
            color: "#fecaca",
            padding: "10px 12px",
            fontSize: 14
          }}
        >
          {error}
        </div>
      ) : null}

      {data ? (
        <div style={{ marginTop: 16, display: "grid", gap: 10 }}>
          <div style={{ color: "rgba(186,230,253,.95)", fontSize: 14 }}>
            Funciones detectadas: {data.detected_functions?.join(", ") || "n/a"}
          </div>
          {(data.results || []).map((r, idx) => (
            <div
              key={`${r.function_name}-${idx}`}
              style={{
                borderRadius: 12,
                border: "1px solid rgba(148,163,184,.35)",
                background: "rgba(15,23,42,.48)",
                padding: "10px 12px"
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <strong style={{ color: "#f8fafc" }}>{r.function_name}</strong>
                <span style={{ color: "#cbd5e1", fontSize: 12 }}>{r.status}</span>
              </div>
              <div style={{ marginTop: 4, color: "rgba(226,232,240,.8)", fontSize: 13 }}>{r.reason}</div>
              {Array.isArray(r.next_actions) && r.next_actions.length ? (
                <div style={{ marginTop: 6, color: "#fcd34d", fontSize: 12 }}>
                  Falta: {r.next_actions.join(" | ")}
                </div>
              ) : null}
              {r.url ? (
                <a
                  href={r.url}
                  target="_blank"
                  rel="noreferrer"
                  style={{ marginTop: 6, display: "inline-block", color: "#93c5fd", textDecoration: "underline", fontSize: 13 }}
                >
                  Abrir workflow
                </a>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </Card>
  );
}
