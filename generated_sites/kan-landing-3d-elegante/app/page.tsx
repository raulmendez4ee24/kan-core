import DashboardPreview from "@/components/sections/DashboardPreview";
import Hero from "@/components/sections/Hero";
import LeadForm from "@/components/sections/LeadForm";
import Pricing from "@/components/sections/Pricing";

const channels = ["Meta: Instagram", "Facebook", "Messenger", "WhatsApp", "Webchat"];
const useCases = [
  "Captura de leads",
  "Calificación automática",
  "Agenda + recordatorios",
  "Seguimiento y reactivación",
  "Soporte 24/7",
  "Reportes y panel",
];
const pipeline = [
  "Auditoría",
  "Diseño de flujos",
  "Implementación",
  "QA",
  "Lanzamiento",
  "Optimización continua",
];
const faqs = [
  "¿Qué necesito para empezar?",
  "¿Cuánto tarda?",
  "¿Se integra con mi CRM?",
  "¿Qué pasa si quiero cambios?",
  "¿Qué incluye la auditoría?",
  "Seguridad y privacidad",
];

export default function Home() {
  return (
    <main>
      <Hero />

      <section className="py-16">
        <div className="container-landing">
          <h2 className="section-title font-semibold">Prueba social y canales activos</h2>
          <div className="mt-5 flex flex-wrap gap-2">
            {channels.map((ch) => (
              <span key={ch} className="rounded-full border border-black/10 bg-white/70 px-3 py-1 text-sm">{ch}</span>
            ))}
          </div>
          <div className="mt-6 grid gap-3 md:grid-cols-3">
            <div className="glass-soft rounded-xl p-5">Respuesta inicial: <strong>&lt; 10s</strong> (objetivo editable)</div>
            <div className="glass-soft rounded-xl p-5">Cobertura operativa: <strong>24/7</strong></div>
            <div className="glass-soft rounded-xl p-5">Menos tareas repetitivas y más foco comercial</div>
          </div>
        </div>
      </section>

      <section className="py-16">
        <div className="container-landing grid gap-6 md:grid-cols-2">
          <article className="rounded-2xl border border-black/10 bg-white/80 p-7 shadow-soft">
            <h3 className="text-2xl font-semibold">Problema</h3>
            <ul className="mt-4 space-y-2 text-slate-700">
              <li>• Leads perdidos por tiempos de respuesta.</li>
              <li>• Procesos manuales lentos y costosos.</li>
              <li>• Citas olvidadas y seguimiento inconsistente.</li>
              <li>• Saturación de equipo en soporte.</li>
            </ul>
          </article>
          <article className="rounded-2xl border border-black/10 bg-ink p-7 text-ivory shadow-soft">
            <h3 className="text-2xl font-semibold">Solución</h3>
            <ul className="mt-4 space-y-2 text-slate-200">
              <li>• Empleados digitales por canal.</li>
              <li>• Flujos de calificación automática.</li>
              <li>• Agenda con recordatorios inteligentes.</li>
              <li>• Panel ejecutivo para decisiones.</li>
            </ul>
          </article>
        </div>
      </section>

      <section className="py-16">
        <div className="container-landing">
          <h2 className="section-title font-semibold">Casos de uso</h2>
          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {useCases.map((item) => (
              <article key={item} className="glass-soft rounded-xl p-5 shadow-soft">{item}</article>
            ))}
          </div>
        </div>
      </section>

      <section className="py-16">
        <div className="container-landing">
          <h2 className="section-title font-semibold">Cómo funciona</h2>
          <div className="mt-6 grid gap-3 md:grid-cols-6">
            {pipeline.map((step, idx) => (
              <div key={step} className="glass-soft rounded-xl p-4 text-sm">
                <div className="mb-2 text-xs text-slate-500">Paso {idx + 1}</div>
                <div className="font-medium">{step}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <DashboardPreview />
      <Pricing />

      <section className="py-16">
        <div className="container-landing">
          <h2 className="section-title font-semibold">FAQ</h2>
          <div className="mt-6 grid gap-3">
            {faqs.map((q) => (
              <details key={q} className="rounded-xl border border-black/10 bg-white/80 p-4">
                <summary className="cursor-pointer font-medium">{q}</summary>
                <p className="mt-2 text-sm text-slate-600">Respuesta editable en implementación final según operación y stack del cliente.</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <LeadForm />

      <footer className="border-t border-black/10 py-10">
        <div className="container-landing flex flex-wrap items-center justify-between gap-3 text-sm text-slate-600">
          <p>© {new Date().getFullYear()} KAN. Arquitectura IA para negocio real.</p>
          <div className="flex gap-4">
            <a href="#">Privacidad</a>
            <a href="#">Términos</a>
            <a href="#">Contacto</a>
          </div>
        </div>
      </footer>
    </main>
  );
}
