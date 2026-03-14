import Link from "next/link";

import { LocalStatusPanel } from "@/components/local-status-panel";
import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function HomePage() {
  return (
    <main>
      <Topbar />

      <div className="mb-6">
        <LocalStatusPanel />
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card className="p-8">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight text-ink-900 sm:text-4xl">
            Localhost de guerra. Control total en un solo panel.
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-black/70">
            Corre Task Studio, autonomy v2 y evidence tracking con feedback de estado en vivo.
            Menos tiempo debugueando, mas tiempo ejecutando.
          </p>

          <div className="mt-6 flex flex-wrap gap-3">
            <Link href="/login">
              <Button>Entrar Console</Button>
            </Link>
            <Link href="/autonomy">
              <Button variant="secondary">Abrir Autonomy</Button>
            </Link>
            <Link href="/dashboard">
              <Button variant="ghost">Dashboard</Button>
            </Link>
          </div>
        </Card>

        <Card className="p-8">
          <div className="text-xs font-medium uppercase tracking-wider text-black/50">Quick start local</div>
          <div className="mt-4 space-y-3 text-sm text-black/75">
            <p>
              <span className="font-medium text-ink-900">1.</span> Levanta stack:
              <code className="ml-2 rounded bg-black/5 px-2 py-1">kan up</code>
            </p>
            <p>
              <span className="font-medium text-ink-900">2.</span> Verifica:
              <code className="ml-2 rounded bg-black/5 px-2 py-1">kan doctor</code>
            </p>
            <p>
              <span className="font-medium text-ink-900">3.</span> Frontend:
              <code className="ml-2 rounded bg-black/5 px-2 py-1">cd web && npm run dev -- --port 3000</code>
            </p>
          </div>
        </Card>
      </div>
    </main>
  );
}
