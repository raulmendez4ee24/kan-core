import Link from "next/link";

import { DemoChat } from "@/app/trial/demo/DemoChat";
import { Topbar } from "@/components/topbar";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { consoleFetch } from "@/lib/backend";

type Task = { id: string; title: string; status: string };

export default async function TrialDemoPage() {
  const res = await consoleFetch("/console/tasks");
  const tasks = res.ok ? (((await res.json()) as { items: Task[] })?.items || []) : [];

  return (
    <main>
      <Topbar />

      <div className="grid gap-6 md:grid-cols-5">
        <div className="md:col-span-3">
          <DemoChat />
        </div>
        <div className="md:col-span-2">
          <Card>
            <h2 className="text-lg font-semibold tracking-tight">Checklist</h2>
            <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-black/75">
              <li>Escribe: "Hola, quiero una cita".</li>
              <li>Verifica que pida nombre, motivo y disponibilidad.</li>
              <li>Corre el task del pack para ver instalacion/autopilot.</li>
            </ol>
          </Card>

          <Card className="mt-6">
            <h2 className="text-lg font-semibold tracking-tight">Tasks instalados</h2>
            <p className="mt-1 text-sm text-black/70">
              Si instalaste el pack Clinica, deberias ver al menos 1 task.
            </p>

            <div className="mt-5 space-y-3">
              {tasks.map((t) => (
                <Link
                  key={t.id}
                  href={`/tasks/${t.id}`}
                  className="block rounded-xl bg-white/70 px-4 py-3 ring-1 ring-black/10 hover:bg-white"
                >
                  <div className="truncate text-sm font-medium">{t.title}</div>
                  <div className="mt-2 flex gap-2">
                    <Badge className="bg-black/5">{t.status}</Badge>
                  </div>
                </Link>
              ))}
              {!tasks.length ? (
                <div className="text-sm text-black/60">
                  No se encontraron tasks (o no tienes permiso). Revisa flags y prueba
                  instalar pack con el trial.
                </div>
              ) : null}
            </div>
          </Card>
        </div>
      </div>
    </main>
  );
}

