"use client";

import { motion } from "framer-motion";
import Container from "@/components/ui/Container";
import SplineRobot from "@/components/sections/SplineRobot";

export default function Hero() {
  return (
    <section className="relative overflow-hidden pb-20 pt-10 md:pb-24 md:pt-16">
      <Container>
        <div className="grid gap-10 md:grid-cols-[1.1fr_0.9fr] md:items-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: "easeOut" }}
          >
            <p className="mb-4 inline-flex rounded-full border border-black/10 px-3 py-1 text-xs uppercase tracking-[0.14em] text-accent">
              Arquitectura IA para negocio real
            </p>
            <h1 className="section-title max-w-2xl font-semibold text-ink">
              Convierte más con empleados digitales 24/7 que sí responden, califican y agendan.
            </h1>
            <p className="section-sub mt-5 max-w-xl">
              Diseñamos automatizaciones premium para ventas, soporte y operación: chatbots, agentes, flujos y panel con métricas claras.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="#lead-form" className="rounded-full bg-ink px-6 py-3 text-sm font-medium text-ivory">
                Agendar demo
              </a>
              <a href="#planes" className="rounded-full border border-ink/20 bg-white/70 px-6 py-3 text-sm font-medium text-ink">
                Ver planes
              </a>
              <a
                href="https://wa.me/5210000000000"
                target="_blank"
                rel="noreferrer"
                className="rounded-full border border-accent/30 px-6 py-3 text-sm font-medium text-accent"
              >
                WhatsApp
              </a>
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 26 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.9, delay: 0.14 }}
            className="aspect-[4/5] min-h-[340px]"
          >
            <SplineRobot className="h-full w-full" />
          </motion.div>
        </div>
      </Container>
    </section>
  );
}
