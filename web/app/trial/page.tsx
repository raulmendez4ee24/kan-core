import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiBaseUrl } from "@/lib/backend";

async function startTrialAction(formData: FormData) {
  "use server";

  const organization_name = String(formData.get("organization_name") || "").trim();
  const admin_email = String(formData.get("admin_email") || "").trim();
  const admin_password = String(formData.get("admin_password") || "").trim();

  if (!organization_name || !admin_email || !admin_password) {
    throw new Error("missing fields");
  }

  const trialKey = process.env.TRIAL_SIGNUP_KEY || "";
  const res = await fetch(`${apiBaseUrl()}/trial/start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(trialKey ? { "X-Trial-Key": trialKey } : {})
    },
    body: JSON.stringify({
      organization_name,
      admin_email,
      admin_password,
      install_pack_id: "clinic"
    }),
    cache: "no-store"
  });

  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = String(body?.detail || "trial_start_failed");
    redirect(`/trial?error=${encodeURIComponent(detail)}`);
  }

  cookies().set("kan_console_token", String(body.jwt_token || ""), {
    httpOnly: true,
    sameSite: "lax",
    path: "/"
  });
  cookies().set("kan_console_expires_at", String(body.jwt_expires_at || ""), {
    httpOnly: true,
    sameSite: "lax",
    path: "/"
  });
  cookies().set("kan_client_id", String(body.client_id || ""), {
    httpOnly: true,
    sameSite: "lax",
    path: "/"
  });
  cookies().set("kan_client_token", String(body.client_token || ""), {
    httpOnly: true,
    sameSite: "lax",
    path: "/"
  });

  redirect("/trial/demo");
}

export default function TrialPage({
  searchParams
}: {
  searchParams?: { error?: string };
}) {
  const errorMessage = String(searchParams?.error || "").trim();
  return (
    <main>
      <Topbar />

      <div className="mx-auto max-w-xl">
        <Card>
          <h1 className="text-2xl font-semibold tracking-tight">Prueba en 10 min</h1>
          <p className="mt-1 text-sm text-black/70">
            Crea una organizacion + usuario, instala el pack Clinica y abre un demo webchat.
          </p>

          {errorMessage ? (
            <div className="mt-4 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
              Error: {errorMessage}
            </div>
          ) : null}

          <form action={startTrialAction} className="mt-6 space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">Nombre de organizacion</label>
              <Input name="organization_name" placeholder="Clinica Demo" required />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Email admin</label>
              <Input name="admin_email" type="email" placeholder="admin@demo.com" required />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Password</label>
              <Input name="admin_password" type="password" placeholder="••••••••" required />
            </div>

            <Button type="submit" className="w-full">
              Crear trial + abrir demo
            </Button>
          </form>

          <p className="mt-4 text-xs text-black/55">
            Requiere <code>ENABLE_TRIAL_ONBOARDING</code> y <code>MASTER_KEY</code> en el backend.
          </p>
        </Card>
      </div>
    </main>
  );
}
