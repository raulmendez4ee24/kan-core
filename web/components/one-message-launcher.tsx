"use client";

import Link from "next/link";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type LaunchResponse = {
  status?: string;
  run?: {
    run_id?: string;
    status?: string;
    detail?: string;
    summary?: string;
    execution?: {
      summary?: string;
      status?: string;
    } | null;
  };
  error?: string;
};

export function OneMessageLauncher() {
  const [goal, setGoal] = React.useState("");
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string>("");
  const [result, setResult] = React.useState<LaunchResponse | null>(null);

  async function run() {
    if (!goal.trim() || running) return;
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const res = await fetch("/api/autonomy/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal: goal.trim(),
          execution_mode: "balanced",
          max_steps: 8,
          quality_min_score: 0.7,
          enable_self_healing: true,
          self_heal_max_attempts: 3,
          enforce_quality_gate: true,
          dynamic_risk_controls: true,
          auto_learn: true
        })
      });
      const body = (await res.json().catch(() => ({}))) as LaunchResponse;
      if (!res.ok) {
        throw new Error(String(body?.error || "no_se_pudo_ejecutar"));
      }
      setResult(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error_desconocido");
    } finally {
      setRunning(false);
    }
  }

  const runId = result?.run?.run_id || "";
  const runSummary =
    result?.run?.execution?.summary || result?.run?.summary || result?.run?.detail || "";

  return (
    <Card className="md:col-span-2" style={{ borderRadius: 20, padding: 24, background: "linear-gradient(135deg, rgba(18,25,43,.95), rgba(30,41,59,.92))", color: "#f8fafc" }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight" style={{ fontSize: 34, letterSpacing: "-.03em", lineHeight: 1.1 }}>One Message Run</h2>
          <p className="mt-1 text-sm text-black/70" style={{ color: "rgba(241,245,249,.78)", marginTop: 8 }}>
            Escribe una sola instruccion y lo ejecuta en autonomy v2.
          </p>
        </div>
        <Badge className="bg-ink-100 text-ink-900 ring-ink-200" style={{ background: "rgba(148,163,184,.22)", color: "#e2e8f0", borderColor: "rgba(148,163,184,.34)" }}>1 mensaje</Badge>
      </div>

      <div className="mt-4 grid gap-3">
        <textarea
          rows={3}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          className="w-full rounded-xl bg-white/70 px-3 py-2 text-sm ring-1 ring-black/10 placeholder:text-black/40 focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2"
          style={{
            width: "100%",
            borderRadius: 14,
            border: "1px solid rgba(148,163,184,.35)",
            background: "rgba(15,23,42,.55)",
            color: "#f8fafc",
            fontSize: 15,
            padding: "12px 14px"
          }}
          placeholder="Ej: Investiga software de CRM para gimnasio local, compara planes y dame recomendacion accionable."
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={run} disabled={running || !goal.trim()} style={{ background: "#f8fafc", color: "#0f172a", borderRadius: 10, fontWeight: 700 }}>
            {running ? "Ejecutando..." : "Ejecutar"}
          </Button>
          <Link href="/autonomy">
            <Button variant="secondary" style={{ borderRadius: 10, border: "1px solid rgba(148,163,184,.35)", background: "rgba(15,23,42,.3)", color: "#e2e8f0" }}>Abrir panel avanzado</Button>
          </Link>
        </div>
      </div>

      {error ? (
        <div className="mt-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-900 ring-1 ring-rose-200" style={{ background: "rgba(220,38,38,.16)", color: "#fecaca", border: "1px solid rgba(248,113,113,.45)" }}>
          {error}
        </div>
      ) : null}

      {result?.run ? (
        <div className="mt-4 rounded-xl bg-emerald-50 px-3 py-2 text-sm text-emerald-900 ring-1 ring-emerald-200" style={{ background: "rgba(16,185,129,.16)", color: "#d1fae5", border: "1px solid rgba(52,211,153,.45)" }}>
          <div className="font-medium">Run lanzado: {runId.slice(0, 8) || "n/a"}</div>
          {runSummary ? <div className="mt-1 text-emerald-900/90">{runSummary}</div> : null}
          {runId ? (
            <div className="mt-2">
              <Link className="underline" href={`/task-runs/${runId}`}>
                Ver run
              </Link>
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
