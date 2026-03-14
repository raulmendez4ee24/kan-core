"use client";

import { motion } from "framer-motion";

const metrics = [
  { label: "Leads nuevos", value: 128 },
  { label: "Conversaciones", value: 412 },
  { label: "Conversion", value: 22 },
  { label: "Citas agendadas", value: 37 },
];

export default function DashboardPreview() {
  return (
    <section className="py-20">
      <div className="container-landing">
        <h2 className="section-title font-semibold">Preview del dashboard operativo</h2>
        <p className="section-sub mt-3 max-w-2xl">Datos mock listos para conectar con backend real. Visual sobrio y ejecutivo.</p>
        <div className="mt-8 grid gap-4 md:grid-cols-4">
          {metrics.map((item, idx) => (
            <motion.div
              key={item.label}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.35 }}
              transition={{ duration: 0.45, delay: idx * 0.08 }}
              className="glass-soft rounded-xl p-4 shadow-soft"
            >
              <p className="text-xs uppercase tracking-[0.09em] text-slate-500">{item.label}</p>
              <p className="mt-2 text-3xl font-semibold text-ink">{item.value}{item.label === "Conversion" ? "%" : ""}</p>
            </motion.div>
          ))}
        </div>
        <div className="mt-6 rounded-2xl border border-black/10 bg-white/80 p-6 shadow-soft">
          <div className="grid gap-3 md:grid-cols-6">
            {[35, 42, 39, 56, 61, 59].map((h, i) => (
              <div key={i} className="flex h-28 items-end rounded-lg bg-slate-100 p-2">
                <div className="w-full rounded-md bg-accent/80" style={{ height: `${h}%` }} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
