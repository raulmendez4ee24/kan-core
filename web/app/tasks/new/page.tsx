import { redirect } from "next/navigation";

import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { consoleFetch } from "@/lib/backend";

async function createTaskAction(formData: FormData) {
  "use server";

  const title = String(formData.get("title") || "").trim();
  const description = String(formData.get("description") || "").trim();
  const schedule_cron = String(formData.get("schedule_cron") || "").trim();
  const timezone = String(formData.get("timezone") || "UTC").trim() || "UTC";
  const target_env = String(formData.get("target_env") || "production").trim();
  const require_sandbox_pass = String(formData.get("require_sandbox_pass") || "true") === "true";
  const capture_evidence = String(formData.get("capture_evidence") || "true") === "true";
  const definition_json = String(formData.get("definition_json") || "").trim();

  if (!title) throw new Error("title required");

  let definition: any = null;
  try {
    definition = JSON.parse(definition_json);
  } catch {
    throw new Error("definition_json must be valid JSON");
  }

  const res = await consoleFetch("/console/tasks", {
    method: "POST",
    body: JSON.stringify({
      title,
      description: description || null,
      status: "active",
      schedule_cron: schedule_cron || null,
      timezone,
      target_env,
      require_sandbox_pass,
      definition,
      capture_evidence,
      metadata: {}
    })
  });

  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(String(body?.detail || "create_task_failed"));
  }

  redirect(`/tasks/${body.id}`);
}

const DEFAULT_DEFINITION = JSON.stringify(
  {
    mode: "integration_autopilot",
    ref_id: null,
    steps: [],
    params: {
      request_text: "Instalar integraciones base (WhatsApp/CRM/Calendar) y generar workflow en n8n.",
      desired_functions: ["meta", "crm", "calendar", "email"],
      industry: "clinics"
    }
  },
  null,
  2
);

export default function NewTaskPage() {
  return (
    <main>
      <Topbar />

      <div className="mx-auto max-w-2xl">
        <Card>
          <h1 className="text-2xl font-semibold tracking-tight">Crear task</h1>
          <p className="mt-1 text-sm text-black/70">
            Define una mision. Puedes usar <code>integration_autopilot</code> o una secuencia
            de acciones desktop/browser.
          </p>

          <form action={createTaskAction} className="mt-6 space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">Titulo</label>
              <Input name="title" placeholder="Reporte diario / Instalar pack / ..." required />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Descripcion</label>
              <Input name="description" placeholder="Opcional" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium">Target env</label>
                <select
                  name="target_env"
                  className="h-10 w-full rounded-xl bg-white/70 px-3 text-sm ring-1 ring-black/10 focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2"
                  defaultValue="production"
                >
                  <option value="production">production</option>
                  <option value="sandbox">sandbox</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">Timezone</label>
                <Input name="timezone" defaultValue="UTC" />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium">Cron (opcional)</label>
                <Input name="schedule_cron" placeholder="0 9 * * *" />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">Sandbox first</label>
                <select
                  name="require_sandbox_pass"
                  className="h-10 w-full rounded-xl bg-white/70 px-3 text-sm ring-1 ring-black/10 focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2"
                  defaultValue="true"
                >
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              </div>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">Evidence</label>
              <select
                name="capture_evidence"
                className="h-10 w-full rounded-xl bg-white/70 px-3 text-sm ring-1 ring-black/10 focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2"
                defaultValue="true"
              >
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
              <p className="mt-1 text-xs text-black/55">
                Si esta en <code>true</code>, el worker intentara capturar screenshot before/after
                usando el desktop agent (si existe).
              </p>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium">Definition (JSON)</label>
              <textarea
                name="definition_json"
                defaultValue={DEFAULT_DEFINITION}
                className="min-h-[220px] w-full rounded-xl bg-white/70 p-3 font-mono text-xs ring-1 ring-black/10 focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2"
              />
            </div>

            <div className="flex items-center justify-end gap-3">
              <Button variant="secondary" type="reset">
                Reset
              </Button>
              <Button type="submit">Crear</Button>
            </div>
          </form>
        </Card>
      </div>
    </main>
  );
}

