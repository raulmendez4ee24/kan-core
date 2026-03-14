"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type StatusResponse = {
  ok: boolean;
  backend: {
    ok: boolean;
    status: string;
    latency_ms: number | null;
  };
  auth: {
    console_token: boolean;
    client_keys: boolean;
  };
  now: string;
};

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${
        ok ? "bg-emerald-500 shadow-[0_0_0_4px_rgba(16,185,129,.2)]" : "bg-rose-500"
      }`}
    />
  );
}

export function LocalStatusPanel() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  const fetchStatus = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/system/status", { cache: "no-store" });
      const body = (await res.json()) as StatusResponse;
      setData(body);
      if (!res.ok) setError("No se pudo leer estado local.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error desconocido.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchStatus();
    const t = setInterval(() => void fetchStatus(), 10000);
    return () => clearInterval(t);
  }, []);

  const readiness = useMemo(() => {
    if (!data) return 0;
    let score = 0;
    if (data.backend.ok) score += 40;
    if (data.auth.console_token) score += 30;
    if (data.auth.client_keys) score += 30;
    return score;
  }, [data]);

  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-black/10 bg-gradient-to-r from-ink-900 via-slate-800 to-slate-700 px-6 py-5 text-white">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-white/70">Localhost Control</p>
            <h2 className="mt-1 text-xl font-semibold tracking-tight">Estado en vivo del stack</h2>
          </div>
          <Badge className="bg-white/20 text-white ring-white/30">{readiness}% ready</Badge>
        </div>
      </div>

      <div className="grid gap-4 p-6 md:grid-cols-3">
        <div className="rounded-xl border border-black/10 bg-white/70 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase tracking-wider text-black/55">Backend</span>
            <Dot ok={Boolean(data?.backend.ok)} />
          </div>
          <p className="mt-2 text-sm font-medium text-ink-900">
            {data?.backend.ok ? "Activo" : "Caido"}
          </p>
          <p className="mt-1 text-xs text-black/60">
            /healthz {data?.backend.latency_ms ? `(${data.backend.latency_ms}ms)` : ""}
          </p>
        </div>

        <div className="rounded-xl border border-black/10 bg-white/70 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase tracking-wider text-black/55">Console Auth</span>
            <Dot ok={Boolean(data?.auth.console_token)} />
          </div>
          <p className="mt-2 text-sm font-medium text-ink-900">
            {data?.auth.console_token ? "Token presente" : "Sin login"}
          </p>
          <p className="mt-1 text-xs text-black/60">Cookie `kan_console_token`</p>
        </div>

        <div className="rounded-xl border border-black/10 bg-white/70 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase tracking-wider text-black/55">Client Keys</span>
            <Dot ok={Boolean(data?.auth.client_keys)} />
          </div>
          <p className="mt-2 text-sm font-medium text-ink-900">
            {data?.auth.client_keys ? "Listas para autonomy" : "Faltan credenciales"}
          </p>
          <p className="mt-1 text-xs text-black/60">Cookies `kan_client_id` + `kan_client_token`</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-black/10 px-6 py-4">
        <div className="text-xs text-black/60">
          {error
            ? `Error: ${error}`
            : `Ultima lectura: ${data?.now ? new Date(data.now).toLocaleTimeString() : "--:--:--"}`}
        </div>
        <Button variant="secondary" onClick={() => void fetchStatus()} disabled={loading}>
          {loading ? "Actualizando..." : "Refresh"}
        </Button>
      </div>
    </Card>
  );
}

