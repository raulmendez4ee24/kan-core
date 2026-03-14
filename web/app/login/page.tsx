import { Topbar } from "@/components/topbar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function LoginPage({
  searchParams
}: {
  searchParams?: { error?: string };
}) {
  const error = String(searchParams?.error || "").trim();
  return (
    <main>
      <Topbar />

      <div className="mx-auto grid max-w-5xl gap-6 md:grid-cols-[0.95fr_1.05fr]">
        <Card className="p-8">
          <h2 className="text-lg font-semibold tracking-tight">Acceso seguro</h2>
          <p className="mt-2 text-sm text-black/70">
            Usa tus credenciales de consola para operar Tasks, Autonomy y Settings.
          </p>
          <div className="mt-6 space-y-3 text-sm text-black/75">
            <div className="rounded-xl bg-white/70 p-3 ring-1 ring-black/10">
              JWT por organización
            </div>
            <div className="rounded-xl bg-white/70 p-3 ring-1 ring-black/10">
              Sesión en cookie HTTP-only
            </div>
            <div className="rounded-xl bg-white/70 p-3 ring-1 ring-black/10">
              Soporte multi-tenant con Organization ID
            </div>
          </div>
        </Card>

        <Card className="p-8">
          <h1 className="text-3xl font-semibold tracking-tight">Console login</h1>
          <p className="mt-2 text-sm text-black/70">
            Autenticación JWT para endpoints <code>/console/*</code>.
          </p>

          {error ? (
            <div className="mt-4 rounded-xl bg-rose-50 p-3 text-sm text-rose-900 ring-1 ring-rose-200">
              {error}
            </div>
          ) : null}

          <form action="/api/auth/login" method="post" className="mt-6 space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">Email</label>
              <Input name="email" type="email" placeholder="you@company.com" required />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Password</label>
              <Input name="password" type="password" placeholder="••••••••" required />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Organization ID (opcional)</label>
              <Input name="organization_id" placeholder="uuid" />
              <p className="mt-1 text-xs text-black/55">
                Si tu usuario ya tiene <code>organization_id</code>, déjalo vacío.
              </p>
            </div>

            <Button type="submit" className="w-full">
              Iniciar sesión
            </Button>
          </form>
        </Card>
      </div>
    </main>
  );
}
