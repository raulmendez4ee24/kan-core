"use client";

import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Step = {
  step_index: number;
  phase: "observe" | "think" | "act" | "evaluate";
  status: "ok" | "queued" | "blocked" | "failed" | "skipped" | "planned";
  action?: string | null;
  detail?: string | null;
  metadata?: Record<string, unknown>;
};

type ResearchSource = {
  title: string;
  url: string;
  provider: string;
  query: string;
};

type BenchmarkItem = {
  run_id: string;
  goal: string;
  status: string;
  quality_score: number;
  quality_passed: boolean;
  latency_seconds: number;
  sources: number;
  planned_steps: number;
  reliability_index: number;
  timestamp: string;
};

type RunPayload = {
  run_id: string;
  status: string;
  summary?: string;
  detail?: string;
  research?: {
    verified: boolean;
    unique_sources: number;
    providers_used: string[];
    top_sources: ResearchSource[];
  };
  execution?: {
    run_id: string;
    status: string;
    summary: string;
    error?: string | null;
    steps: Step[];
  } | null;
  quality?: Record<string, unknown>;
  benchmark?: BenchmarkItem;
  learning?: Record<string, unknown>;
  next_actions?: string[];
};

type ExecuteResponse = {
  status: string;
  run: RunPayload;
};

function boolFromStorage(key: string, fallback: boolean): boolean {
  if (typeof window === "undefined") return fallback;
  const raw = window.localStorage.getItem(key);
  if (raw === null) return fallback;
  return raw === "1";
}

function strFromStorage(key: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return window.localStorage.getItem(key) || fallback;
}

export function AutonomyControlCenter() {
  const [goal, setGoal] = React.useState("");
  const [startUrl, setStartUrl] = React.useState("");
  const [clientId, setClientId] = React.useState("");
  const [clientToken, setClientToken] = React.useState("");
  const [mode, setMode] = React.useState<"safe" | "balanced" | "autonomous">("balanced");
  const [maxSteps, setMaxSteps] = React.useState("8");
  const [qualityMin, setQualityMin] = React.useState("0.7");
  const [enableHealing, setEnableHealing] = React.useState(true);
  const [healingAttempts, setHealingAttempts] = React.useState("3");
  const [enforceQuality, setEnforceQuality] = React.useState(true);
  const [dynamicRisk, setDynamicRisk] = React.useState(true);
  const [allowHighRisk, setAllowHighRisk] = React.useState(false);
  const [autoLearn, setAutoLearn] = React.useState(true);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<ExecuteResponse | null>(null);
  const [ranking, setRanking] = React.useState<BenchmarkItem[]>([]);
  const [rankingOrder, setRankingOrder] = React.useState<"reliability" | "quality" | "latency">("reliability");
  const [loadingRanking, setLoadingRanking] = React.useState(false);

  React.useEffect(() => {
    setClientId(strFromStorage("kan_autonomy_client_id", ""));
    setClientToken(strFromStorage("kan_autonomy_client_token", ""));
    setAutoLearn(boolFromStorage("kan_autonomy_auto_learn", true));
  }, []);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("kan_autonomy_client_id", clientId);
  }, [clientId]);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("kan_autonomy_client_token", clientToken);
  }, [clientToken]);

  React.useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("kan_autonomy_auto_learn", autoLearn ? "1" : "0");
  }, [autoLearn]);

  const quality = React.useMemo(() => {
    const q = result?.run?.quality || {};
    const qObj = q as Record<string, unknown>;
    return {
      score: qObj?.score,
      threshold: qObj?.threshold,
      passed: qObj?.quality_gate_passed,
      notes: Array.isArray(qObj?.notes) ? qObj?.notes : []
    };
  }, [result]);

  const loadBenchmarks = React.useCallback(async () => {
    setLoadingRanking(true);
    try {
      const qs = new URLSearchParams({
        limit: "25",
        order_by: rankingOrder,
        descending: rankingOrder === "latency" ? "false" : "true"
      });
      if (clientId.trim()) qs.set("client_id", clientId.trim());
      if (clientToken.trim()) qs.set("client_token", clientToken.trim());
      const res = await fetch(`/api/autonomy/benchmarks?${qs.toString()}`);
      const body = (await res.json().catch(() => [])) as BenchmarkItem[] | { error?: string };
      if (!res.ok) {
        throw new Error(String((body as { error?: string })?.error || "ranking_load_failed"));
      }
      setRanking(Array.isArray(body) ? body : []);
    } catch {
      setRanking([]);
    } finally {
      setLoadingRanking(false);
    }
  }, [rankingOrder, clientId, clientToken]);

  React.useEffect(() => {
    void loadBenchmarks();
  }, [loadBenchmarks]);

  async function runGoal() {
    if (!goal.trim() || running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/autonomy/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal: goal.trim(),
          start_url: startUrl.trim() || null,
          execution_mode: mode,
          max_steps: Number(maxSteps || "8"),
          quality_min_score: Number(qualityMin || "0.7"),
          enable_self_healing: enableHealing,
          self_heal_max_attempts: Number(healingAttempts || "3"),
          enforce_quality_gate: enforceQuality,
          dynamic_risk_controls: dynamicRisk,
          allow_high_risk_auto_approval: allowHighRisk,
          auto_learn: autoLearn,
          client_id: clientId.trim() || undefined,
          client_token: clientToken.trim() || undefined
        })
      });
      const body = (await res.json().catch(() => ({}))) as ExecuteResponse & { error?: string };
      if (!res.ok) {
        throw new Error(String(body?.error || "execution_failed"));
      }
      setResult(body);
      await loadBenchmarks();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setRunning(false);
    }
  }

  const run = result?.run;
  const execution = run?.execution;

  return (
    <div className="grid gap-6">
      <Card>
        <h1 className="text-2xl font-semibold tracking-tight">Autonomy Control Center</h1>
        <p className="mt-2 text-sm text-black/70">
          Ejecuta objetivos locales con web research, self-healing, quality gate y ranking persistente.
        </p>

        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <div className="md:col-span-2">
            <label className="mb-1 block text-sm font-medium">Objetivo</label>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              rows={4}
              className="w-full rounded-xl bg-white/70 px-3 py-2 text-sm ring-1 ring-black/10 focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2"
              placeholder="Ej: Investiga 3 CRMs para dental, compara pricing y crea propuesta de automatización."
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">URL inicial (opcional)</label>
            <Input value={startUrl} onChange={(e) => setStartUrl(e.target.value)} placeholder="https://..." />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Modo</label>
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as "safe" | "balanced" | "autonomous")}
              className="h-10 w-full rounded-xl bg-white/70 px-3 text-sm ring-1 ring-black/10 focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2"
            >
              <option value="safe">safe</option>
              <option value="balanced">balanced</option>
              <option value="autonomous">autonomous</option>
            </select>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Max steps</label>
            <Input value={maxSteps} onChange={(e) => setMaxSteps(e.target.value)} />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Quality min score (0-1)</label>
            <Input value={qualityMin} onChange={(e) => setQualityMin(e.target.value)} />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Client ID (opcional)</label>
            <Input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="uuid" />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Client Token (opcional)</label>
            <Input
              type="password"
              value={clientToken}
              onChange={(e) => setClientToken(e.target.value)}
              placeholder="token"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Self-heal attempts</label>
            <Input value={healingAttempts} onChange={(e) => setHealingAttempts(e.target.value)} />
          </div>

          <div className="flex flex-wrap items-center gap-4 pt-7 text-sm">
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={enableHealing} onChange={(e) => setEnableHealing(e.target.checked)} />
              self-healing
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={enforceQuality} onChange={(e) => setEnforceQuality(e.target.checked)} />
              quality gate
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={dynamicRisk} onChange={(e) => setDynamicRisk(e.target.checked)} />
              riesgo dinámico
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={autoLearn} onChange={(e) => setAutoLearn(e.target.checked)} />
              auto-aprendizaje
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={allowHighRisk} onChange={(e) => setAllowHighRisk(e.target.checked)} />
              high-risk auto-approval
            </label>
          </div>
        </div>

        <div className="mt-6 flex items-center gap-3">
          <Button onClick={runGoal} disabled={running || !goal.trim()}>
            {running ? "Ejecutando..." : "Ejecutar objetivo"}
          </Button>
          <Badge className="bg-black/5">v2 orchestration</Badge>
        </div>

        {error ? (
          <div className="mt-4 rounded-xl bg-rose-50 p-3 text-sm text-rose-900 ring-1 ring-rose-200">{error}</div>
        ) : null}
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold tracking-tight">Ranking de Runs (persistente)</h2>
          <div className="flex items-center gap-2">
            <select
              value={rankingOrder}
              onChange={(e) => setRankingOrder(e.target.value as "reliability" | "quality" | "latency")}
              className="h-9 rounded-lg bg-white/70 px-2 text-sm ring-1 ring-black/10"
            >
              <option value="reliability">reliability</option>
              <option value="quality">quality</option>
              <option value="latency">latency</option>
            </select>
            <Button variant="secondary" onClick={() => void loadBenchmarks()} disabled={loadingRanking}>
              {loadingRanking ? "Cargando..." : "Refrescar"}
            </Button>
          </div>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-black/60">
                <th className="px-2 py-2">Run</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-2 py-2">Reliability</th>
                <th className="px-2 py-2">Quality</th>
                <th className="px-2 py-2">Latency</th>
                <th className="px-2 py-2">Sources</th>
                <th className="px-2 py-2">Fecha</th>
              </tr>
            </thead>
            <tbody>
              {ranking.map((item) => (
                <tr key={item.run_id} className="border-t border-black/10">
                  <td className="px-2 py-2 font-mono text-xs">{item.run_id.slice(0, 8)}</td>
                  <td className="px-2 py-2">
                    <Badge className="bg-black/5">{item.status}</Badge>
                  </td>
                  <td className="px-2 py-2">{item.reliability_index.toFixed(3)}</td>
                  <td className="px-2 py-2">{item.quality_score.toFixed(3)}</td>
                  <td className="px-2 py-2">{item.latency_seconds.toFixed(1)}s</td>
                  <td className="px-2 py-2">{item.sources}</td>
                  <td className="px-2 py-2">{new Date(item.timestamp).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!ranking.length ? (
            <div className="rounded-lg bg-white/70 px-3 py-3 text-sm text-black/60 ring-1 ring-black/10">
              Sin runs guardados aún para esta organización.
            </div>
          ) : null}
        </div>
      </Card>

      {run ? (
        <Card>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold tracking-tight">Resultado</h2>
            <Badge>{run.status}</Badge>
            {execution?.status ? <Badge className="bg-ink-100 text-ink-900 ring-ink-200">{execution.status}</Badge> : null}
          </div>

          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <div className="rounded-xl bg-white/70 p-4 ring-1 ring-black/10">
              <div className="text-xs text-black/60">Fuentes verificadas</div>
              <div className="mt-1 text-2xl font-semibold">{run.research?.unique_sources || 0}</div>
            </div>
            <div className="rounded-xl bg-white/70 p-4 ring-1 ring-black/10">
              <div className="text-xs text-black/60">Quality score</div>
              <div className="mt-1 text-2xl font-semibold">{String(quality.score ?? "-")}</div>
              <div className="mt-2 text-xs text-black/60">
                threshold: {String(quality.threshold ?? "-")} / passed: {String(quality.passed ?? "-")}
              </div>
            </div>
            <div className="rounded-xl bg-white/70 p-4 ring-1 ring-black/10">
              <div className="text-xs text-black/60">Run ID</div>
              <div className="mt-2 break-all font-mono text-xs">{run.run_id || execution?.run_id || "-"}</div>
            </div>
          </div>

          {run.next_actions?.length ? (
            <div className="mt-5 rounded-xl bg-white/70 p-4 ring-1 ring-black/10">
              <h3 className="text-sm font-medium">Next actions</h3>
              <ul className="mt-2 space-y-1 text-sm text-black/80">
                {run.next_actions.slice(0, 8).map((x) => (
                  <li key={x}>• {x}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {run.research?.top_sources?.length ? (
            <div className="mt-5">
              <h3 className="text-sm font-medium">Top fuentes</h3>
              <div className="mt-2 space-y-2">
                {run.research.top_sources.slice(0, 6).map((s) => (
                  <a
                    key={s.url}
                    href={s.url}
                    target="_blank"
                    rel="noreferrer"
                    className="block rounded-lg bg-white/70 px-3 py-2 text-sm ring-1 ring-black/10 hover:bg-white"
                  >
                    <div className="font-medium">{s.title}</div>
                    <div className="mt-1 text-xs text-black/60">{s.provider}</div>
                  </a>
                ))}
              </div>
            </div>
          ) : null}

          {execution?.steps?.length ? (
            <div className="mt-5">
              <h3 className="text-sm font-medium">Trazabilidad de pasos</h3>
              <div className="mt-2 space-y-2">
                {execution.steps.map((step) => (
                  <div key={`${step.step_index}-${step.phase}`} className="rounded-lg bg-white/70 px-3 py-2 text-sm ring-1 ring-black/10">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge>#{step.step_index}</Badge>
                      <Badge className="bg-black/5">{step.phase}</Badge>
                      <Badge className="bg-black/5">{step.status}</Badge>
                      {step.action ? <Badge className="bg-ink-100 text-ink-900 ring-ink-200">{step.action}</Badge> : null}
                    </div>
                    {step.detail ? <p className="mt-2 text-xs text-black/70">{step.detail}</p> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}
