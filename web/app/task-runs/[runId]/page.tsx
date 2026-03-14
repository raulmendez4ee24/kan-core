import Link from "next/link";

import { Topbar } from "@/components/topbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { consoleFetch } from "@/lib/backend";

type TaskRun = {
  id: string;
  task_id: string;
  status: string;
  environment: string;
  correlation_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  result?: any;
};

type Step = {
  id: string;
  step_order: number;
  kind: string;
  title: string;
  action?: string | null;
  status: string;
  attempts: number;
  reasoning?: string | null;
  evidence_before_asset_id?: string | null;
  evidence_after_asset_id?: string | null;
  result: any;
};

async function signedUrl(assetId: string) {
  const res = await consoleFetch(`/console/evidence/${assetId}/signed-url?expires_seconds=900`);
  if (!res.ok) return null;
  const body = (await res.json()) as { url: string };
  return body.url || null;
}

export default async function TaskRunPage({
  params
}: {
  params: { runId: string };
}) {
  const runId = params.runId;

  const [runRes, stepsRes] = await Promise.all([
    consoleFetch(`/console/task-runs/${runId}`),
    consoleFetch(`/console/task-runs/${runId}/steps`)
  ]);

  if (!runRes.ok) {
    return (
      <main>
        <Topbar />
        <Card>
          <div className="text-sm text-black/70">Run no encontrado.</div>
        </Card>
      </main>
    );
  }

  const run = (await runRes.json()) as TaskRun;
  const stepsBody = stepsRes.ok ? ((await stepsRes.json()) as { items: Step[] }) : { items: [] };
  const steps = stepsBody.items || [];

  const evidenceMap = new Map<string, string | null>();
  for (const step of steps) {
    if (step.evidence_before_asset_id && !evidenceMap.has(step.evidence_before_asset_id)) {
      evidenceMap.set(step.evidence_before_asset_id, await signedUrl(step.evidence_before_asset_id));
    }
    if (step.evidence_after_asset_id && !evidenceMap.has(step.evidence_after_asset_id)) {
      evidenceMap.set(step.evidence_after_asset_id, await signedUrl(step.evidence_after_asset_id));
    }
  }

  return (
    <main>
      <Topbar />

      <Card>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight">
              Run <span className="font-mono text-sm">{run.id}</span>
            </h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <Badge className="bg-black/5">{run.status}</Badge>
              <Badge className="bg-ink-100 text-ink-900 ring-ink-200">
                {run.environment}
              </Badge>
              {run.error ? (
                <Badge className="bg-red-100 text-red-800 ring-red-200">
                  error
                </Badge>
              ) : null}
            </div>
            <div className="mt-3 text-xs text-black/55">
              correlation_id: <span className="font-mono">{run.correlation_id || "-"}</span>
            </div>
          </div>

          <Link href={`/tasks/${run.task_id}`}>
            <Button variant="secondary">Ver task</Button>
          </Link>
        </div>

        {run.error ? (
          <div className="mt-5 rounded-xl bg-red-50 p-4 text-sm text-red-800 ring-1 ring-red-200">
            {run.error}
          </div>
        ) : null}
      </Card>

      <div className="mt-6 grid gap-6">
        {steps.map((s) => {
          const beforeUrl = s.evidence_before_asset_id ? evidenceMap.get(s.evidence_before_asset_id) : null;
          const afterUrl = s.evidence_after_asset_id ? evidenceMap.get(s.evidence_after_asset_id) : null;

          return (
            <Card key={s.id}>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="text-xs font-medium uppercase tracking-wider text-black/50">
                    Step {s.step_order} • {s.kind}
                  </div>
                  <h2 className="mt-1 truncate text-lg font-semibold tracking-tight">
                    {s.title}
                  </h2>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge className="bg-black/5">{s.status}</Badge>
                    {s.action ? (
                      <Badge className="bg-ink-100 text-ink-900 ring-ink-200">
                        {s.action}
                      </Badge>
                    ) : null}
                    <Badge className="bg-black/5">attempts: {s.attempts}</Badge>
                  </div>
                  {s.reasoning ? (
                    <p className="mt-3 text-sm text-black/70">{s.reasoning}</p>
                  ) : null}
                </div>

                <div className="text-xs text-black/55">
                  <div className="font-mono">{s.id.slice(0, 8)}</div>
                </div>
              </div>

              <div className="mt-5 grid gap-4 md:grid-cols-2">
                <div>
                  <div className="text-xs font-medium uppercase tracking-wider text-black/50">
                    Before
                  </div>
                  {beforeUrl ? (
                    <img
                      src={beforeUrl}
                      alt="before"
                      className="mt-2 w-full rounded-xl ring-1 ring-black/10"
                    />
                  ) : (
                    <div className="mt-2 rounded-xl bg-black/5 p-4 text-sm text-black/60 ring-1 ring-black/10">
                      Sin evidencia before.
                    </div>
                  )}
                </div>
                <div>
                  <div className="text-xs font-medium uppercase tracking-wider text-black/50">
                    After
                  </div>
                  {afterUrl ? (
                    <img
                      src={afterUrl}
                      alt="after"
                      className="mt-2 w-full rounded-xl ring-1 ring-black/10"
                    />
                  ) : (
                    <div className="mt-2 rounded-xl bg-black/5 p-4 text-sm text-black/60 ring-1 ring-black/10">
                      Sin evidencia after.
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-5">
                <div className="text-xs font-medium uppercase tracking-wider text-black/50">
                  Result
                </div>
                <pre className="mt-2 max-h-[320px] overflow-auto rounded-xl bg-black/5 p-4 text-xs ring-1 ring-black/10">
                  {JSON.stringify(s.result, null, 2)}
                </pre>
              </div>
            </Card>
          );
        })}

        {!steps.length ? (
          <Card>
            <div className="text-sm text-black/70">
              No hay steps para este run.
            </div>
          </Card>
        ) : null}
      </div>
    </main>
  );
}

