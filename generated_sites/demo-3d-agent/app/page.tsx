"use client";
import dynamic from "next/dynamic";
import { motion } from "framer-motion";
import ControlPanel from "@/components/hero/ControlPanel";

const Hero3D = dynamic(() => import("@/components/hero/Hero3D"), { ssr: false });

export default function Page() {
  return (
    <main className="min-h-screen">
      <ControlPanel />
      <section className="relative h-[88vh] overflow-hidden">
        <Hero3D />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="glass max-w-3xl rounded-2xl p-8 text-center">
            <motion.h1 initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} className="text-5xl font-semibold tracking-tight">
              KAN CREATIVE 3D WEB AGENT
            </motion.h1>
            <p className="mt-4 text-lg text-cyan-100/80">Cinematic, immersive, high-performance web experiences.</p>
            <div className="mt-6 flex justify-center gap-3">
              <button className="rounded-full bg-cyan-400 px-6 py-3 text-black">Start Project</button>
              <button className="rounded-full border border-cyan-300 px-6 py-3">View Demo</button>
            </div>
          </div>
        </div>
      </section>
      <section className="mx-auto grid max-w-6xl gap-8 px-6 py-16 md:grid-cols-3">
        {["Valor", "Servicios", "Como funciona", "Demo", "Planes", "CTA final"].map((item) => (
          <article key={item} className="glass rounded-xl p-5">
            <h2 className="text-xl font-medium">{item}</h2>
            <p className="mt-2 text-sm text-slate-200/85">Modulo premium para storytelling y conversion con look sci-fi AAA.</p>
          </article>
        ))}
      </section>
      <section className="mx-auto max-w-3xl px-6 pb-24">
        <form className="glass rounded-2xl p-8">
          <h3 className="text-2xl font-semibold">Lead Capture</h3>
          <div className="mt-4 grid gap-3">
            <input className="rounded bg-black/40 p-3" placeholder="Nombre" required />
            <input type="email" className="rounded bg-black/40 p-3" placeholder="Email" required />
            <textarea className="rounded bg-black/40 p-3" placeholder="Objetivo del proyecto" required />
          </div>
          <button className="mt-4 rounded-full bg-white px-6 py-3 text-black">Enviar</button>
        </form>
      </section>
    </main>
  );
}
