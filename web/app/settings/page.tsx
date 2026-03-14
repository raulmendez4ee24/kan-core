import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { consoleFetch } from "@/lib/backend";

type LlmStatus = {
  provider: string;
  configured: boolean;
  integration_name: string | null;
  model_name: string | null;
  secret_backend: string | null;
};

async function loadStatus() {
  const res = await consoleFetch("/console/llm/status?provider=openai");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    return {
      ok: false,
      error: String(body?.detail || body?.error || "No se pudo leer estado LLM.")
    };
  }
  return { ok: true, status: body as LlmStatus };
}

export default async function SettingsPage({
  searchParams
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const statusResult = await loadStatus();
  const status = statusResult.ok ? statusResult.status : null;
  const okParam = String(searchParams?.ok || "");
  const errorParam = String(searchParams?.error || "");

  return (
    <main>
      <Topbar />

      <div className="mx-auto grid max-w-3xl gap-6">
        <Card>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="mt-1 text-sm text-black/70">
            Conecta tu OpenAI API key por organizacion para usar tu propio acceso.
          </p>

          {okParam ? (
            <div className="mt-4 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900 ring-1 ring-emerald-200">
              OpenAI conectado correctamente.
            </div>
          ) : null}
          {errorParam ? (
            <div className="mt-4 rounded-xl bg-rose-50 p-3 text-sm text-rose-900 ring-1 ring-rose-200">
              {errorParam}
            </div>
          ) : null}

          {!statusResult.ok ? (
            <div className="mt-4 rounded-xl bg-black/5 p-4 text-sm text-black/70 ring-1 ring-black/10">
              {statusResult.error}
            </div>
          ) : (
            <div className="mt-4 grid gap-2 rounded-xl bg-white/70 p-4 text-sm ring-1 ring-black/10">
              <div>
                <span className="text-black/60">Provider:</span> {status?.provider}
              </div>
              <div>
                <span className="text-black/60">Configurado:</span>{" "}
                {status?.configured ? "si" : "no"}
              </div>
              <div>
                <span className="text-black/60">Integracion:</span>{" "}
                {status?.integration_name || "-"}
              </div>
              <div>
                <span className="text-black/60">Modelo:</span> {status?.model_name || "-"}
              </div>
              <div>
                <span className="text-black/60">Secret backend:</span>{" "}
                {status?.secret_backend || "-"}
              </div>
            </div>
          )}
        </Card>

        <Card>
          <h2 className="text-lg font-semibold tracking-tight">Conectar OpenAI</h2>
          <form action="/api/llm/connect" method="post" className="mt-4 space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">OpenAI API key</label>
              <Input
                name="api_key"
                type="password"
                placeholder="sk-..."
                autoComplete="off"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Modelo (opcional)</label>
              <Input
                name="model_name"
                defaultValue={status?.model_name || "gpt-5.2-chat-latest"}
                placeholder="gpt-5.2-chat-latest"
              />
            </div>
            <Button type="submit">Guardar y conectar</Button>
          </form>
          <p className="mt-3 text-xs text-black/55">
            Requiere <code>ENABLE_ENTERPRISE_CONSOLE=true</code>. La key se guarda cifrada (o en
            Vault si esta habilitado).
          </p>
        </Card>
      </div>
    </main>
  );
}
