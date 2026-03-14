"use client";

import { useMemo, useState } from "react";

type Plan = {
  name: string;
  monthly: number;
  yearly: number;
  description: string;
  bullets: string[];
  cta: string;
  featured?: boolean;
};

const plans: Plan[] = [
  {
    name: "Starter",
    monthly: 499,
    yearly: 449,
    description: "Ideal para iniciar automatización comercial.",
    bullets: [
      "IG/FB/Messenger/Webchat",
      "2 automatizaciones clave",
      "Panel básico",
      "Soporte estándar",
      "Integración inicial",
      "Ajustes mensuales",
    ],
    cta: "Empezar",
  },
  {
    name: "Pro",
    monthly: 1099,
    yearly: 979,
    description: "Escala ventas y operación con más cobertura.",
    bullets: [
      "IG/FB/Messenger/WhatsApp/Webchat",
      "6 automatizaciones",
      "Panel avanzado",
      "Calificación inteligente",
      "SLA prioritario",
      "Optimización quincenal",
    ],
    cta: "Empezar",
  },
  {
    name: "Enterprise (Auditoría)",
    monthly: 2499,
    yearly: 2199,
    description: "Arquitectura completa con consultoría estratégica.",
    bullets: [
      "Todos los canales + CRM",
      "Automatizaciones ilimitadas",
      "Auditoría IA completa",
      "Panel ejecutivo",
      "SLA enterprise",
      "Roadmap trimestral",
    ],
    cta: "Agendar auditoría",
    featured: true,
  },
];

export default function Pricing() {
  const [yearly, setYearly] = useState(false);
  const unit = yearly ? "USD/mes (anual)" : "USD/mes";
  const data = useMemo(() => plans, []);

  return (
    <section id="planes" className="py-20">
      <div className="container-landing">
        <div className="mb-10 flex items-end justify-between gap-4">
          <div>
            <h2 className="section-title font-semibold">Planes para crecer sin fricción</h2>
            <p className="section-sub mt-3">Estructura clara, SLA definido y evolución continua.</p>
          </div>
          <button
            type="button"
            onClick={() => setYearly((v) => !v)}
            className="rounded-full border border-black/15 bg-white px-4 py-2 text-sm"
          >
            {yearly ? "Anual" : "Mensual"}
          </button>
        </div>
        <div className="grid gap-6 md:grid-cols-3">
          {data.map((plan) => {
            const price = yearly ? plan.yearly : plan.monthly;
            return (
              <article
                key={plan.name}
                className={`rounded-2xl border p-6 shadow-soft ${plan.featured ? "border-accent bg-ink text-ivory" : "border-black/10 bg-white/85"}`}
              >
                <p className="text-sm uppercase tracking-[0.12em] opacity-80">{plan.name}</p>
                <p className="mt-3 text-4xl font-semibold">${price}</p>
                <p className="mt-1 text-sm opacity-80">{unit}</p>
                <p className="mt-4 text-sm opacity-90">{plan.description}</p>
                <ul className="mt-5 space-y-2 text-sm">
                  {plan.bullets.map((bullet) => (
                    <li key={bullet}>• {bullet}</li>
                  ))}
                </ul>
                <button className={`mt-6 w-full rounded-full px-4 py-2 text-sm font-medium ${plan.featured ? "bg-ivory text-ink" : "bg-ink text-ivory"}`}>
                  {plan.cta}
                </button>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
