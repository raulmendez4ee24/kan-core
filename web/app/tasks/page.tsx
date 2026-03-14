import Link from "next/link";

import { Topbar } from "@/components/topbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { consoleFetch } from "@/lib/backend";

type Task = {
  id: string;
  title: string;
  status: string;
  schedule_cron?: string | null;
  timezone: string;
  target_env: string;
};

export default async function TasksPage() {
  const res = await consoleFetch("/console/tasks");
  const body = res.ok ? ((await res.json()) as { items: Task[] }) : null;

  return (
    <main>
      <Topbar />

      <Card>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
            <p className="mt-1 text-sm text-black/70">
              Define misiones recurrentes o manuales. Cada run queda auditado.
            </p>
          </div>
          <Link href="/tasks/new">
            <Button>Crear task</Button>
          </Link>
        </div>

        {!res.ok ? (
          <div className="mt-5 rounded-xl bg-black/5 p-4 text-sm text-black/70 ring-1 ring-black/10">
            No se pudo cargar tasks.
          </div>
        ) : (
          <div className="mt-6 space-y-3">
            {(body?.items || []).map((t) => (
              <Link
                key={t.id}
                href={`/tasks/${t.id}`}
                className="flex items-center justify-between rounded-xl bg-white/70 px-4 py-3 ring-1 ring-black/10 hover:bg-white"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{t.title}</div>
                  <div className="mt-1 flex flex-wrap gap-2">
                    <Badge className="bg-black/5">{t.status}</Badge>
                    <Badge className="bg-ink-100 text-ink-900 ring-ink-200">
                      {t.target_env}
                    </Badge>
                    {t.schedule_cron ? (
                      <Badge className="bg-black/5">
                        cron: <span className="font-mono">{t.schedule_cron}</span>
                      </Badge>
                    ) : null}
                  </div>
                </div>
                <div className="text-xs text-black/50">Abrir</div>
              </Link>
            ))}

            {!body?.items?.length ? (
              <div className="text-sm text-black/60">
                Todavia no hay tasks.
              </div>
            ) : null}
          </div>
        )}
      </Card>
    </main>
  );
}

