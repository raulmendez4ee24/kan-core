import Link from "next/link";
import { redirect } from "next/navigation";

import { Topbar } from "@/components/topbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { consoleFetch } from "@/lib/backend";

type Task = {
  id: string;
  title: string;
  description?: string | null;
  status: string;
  schedule_cron?: string | null;
  timezone: string;
  target_env: string;
  require_sandbox_pass: boolean;
  capture_evidence: boolean;
  definition: any;
};

type TaskRun = {
  id: string;
  status: string;
  environment: string;
  created_at: string;
  error?: string | null;
};

async function runTaskAction(formData: FormData) {
  "use server";

  const taskId = String(formData.get("task_id") || "").trim();
  if (!taskId) throw new Error("task_id required");

  const res = await consoleFetch(`/console/tasks/${taskId}/run`, {
    method: "POST",
    body: JSON.stringify({ environment: null, triggered_by: "manual" })
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(String(body?.detail || "run_task_failed"));
  }
  redirect(`/task-runs/${body.run_id}`);
}

export default async function TaskDetailPage({
  params
}: {
  params: { id: string };
}) {
  const taskId = params.id;
  const [taskRes, runsRes] = await Promise.all([
    consoleFetch(`/console/tasks/${taskId}`),
    consoleFetch(`/console/task-runs?limit=20&task_id=${encodeURIComponent(taskId)}`)
  ]);

  if (!taskRes.ok) {
    return (
      <main>
        <Topbar />
        <Card>
          <div className="text-sm text-black/70">
            No se pudo cargar el task.
          </div>
        </Card>
      </main>
    );
  }

  const task = (await taskRes.json()) as Task;
  const runs = runsRes.ok ? ((await runsRes.json()) as TaskRun[]) : [];

  return (
    <main>
      <Topbar />

      <div className="grid gap-6 md:grid-cols-5">
        <div className="md:col-span-3">
          <Card>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h1 className="truncate text-2xl font-semibold tracking-tight">
                  {task.title}
                </h1>
                <p className="mt-1 text-sm text-black/70">
                  {task.description || "Sin descripcion."}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Badge className="bg-black/5">{task.status}</Badge>
                  <Badge className="bg-ink-100 text-ink-900 ring-ink-200">
                    {task.target_env}
                  </Badge>
                  {task.schedule_cron ? (
                    <Badge className="bg-black/5">
                      cron: <span className="font-mono">{task.schedule_cron}</span>
                    </Badge>
                  ) : null}
                </div>
              </div>

              <form action={runTaskAction}>
                <input type="hidden" name="task_id" value={task.id} />
                <Button type="submit">Run</Button>
              </form>
            </div>

            <div className="mt-6">
              <div className="text-xs font-medium uppercase tracking-wider text-black/50">
                Definition
              </div>
              <pre className="mt-2 overflow-auto rounded-xl bg-black/5 p-4 text-xs ring-1 ring-black/10">
                {JSON.stringify(task.definition, null, 2)}
              </pre>
            </div>
          </Card>
        </div>

        <div className="md:col-span-2">
          <Card>
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold tracking-tight">Runs</h2>
              <Link href="/tasks" className="text-sm text-black/60 underline">
                Volver
              </Link>
            </div>

            <div className="mt-5 space-y-3">
              {runs.map((r) => (
                <Link
                  key={r.id}
                  href={`/task-runs/${r.id}`}
                  className="block rounded-xl bg-white/70 px-4 py-3 ring-1 ring-black/10 hover:bg-white"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm font-medium">
                      <span className="font-mono text-xs">{r.id.slice(0, 8)}</span>
                    </div>
                    <div className="text-xs text-black/50">
                      {new Date(r.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge className="bg-black/5">{r.status}</Badge>
                    <Badge className="bg-ink-100 text-ink-900 ring-ink-200">
                      {r.environment}
                    </Badge>
                    {r.error ? (
                      <Badge className="bg-red-100 text-red-800 ring-red-200">
                        error
                      </Badge>
                    ) : null}
                  </div>
                </Link>
              ))}
              {!runs.length ? (
                <div className="text-sm text-black/60">
                  Aun no hay runs para este task.
                </div>
              ) : null}
            </div>
          </Card>
        </div>
      </div>
    </main>
  );
}

