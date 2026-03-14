"use client";
import { useEffect } from "react";
import { useSceneStore, type HeroVariant, type QualityTier } from "@/components/state/useSceneStore";

export default function ControlPanel() {
  const panelOpen = useSceneStore((s) => s.panelOpen);
  const setPanelOpen = useSceneStore((s) => s.setPanelOpen);
  const hero = useSceneStore((s) => s.hero);
  const quality = useSceneStore((s) => s.quality);
  const bloom = useSceneStore((s) => s.bloom);
  const fog = useSceneStore((s) => s.fog);
  const particles = useSceneStore((s) => s.particles);
  const setHero = useSceneStore((s) => s.setHero);
  const setQuality = useSceneStore((s) => s.setQuality);
  const setBloom = useSceneStore((s) => s.setBloom);
  const setFog = useSceneStore((s) => s.setFog);
  const setParticles = useSceneStore((s) => s.setParticles);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key.toLowerCase() === "p") setPanelOpen(!panelOpen);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [panelOpen, setPanelOpen]);

  if (!panelOpen) return null;
  return (
    <aside className="glass fixed right-5 top-5 z-50 w-80 rounded-xl p-4 text-sm">
      <h3 className="mb-3 text-base font-semibold">Hidden Panel (P)</h3>
      <label className="mb-2 block">
        Hero Variant
        <select className="mt-1 w-full rounded bg-black/40 p-2" value={hero} onChange={(e) => setHero(e.target.value as HeroVariant)}>
          <option value="A">A - Energy Core</option>
          <option value="B">B - Android</option>
          <option value="C">C - Portal</option>
        </select>
      </label>
      <label className="mb-2 block">
        Quality
        <select className="mt-1 w-full rounded bg-black/40 p-2" value={quality} onChange={(e) => setQuality(e.target.value as QualityTier)}>
          <option>ULTRA</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option>
        </select>
      </label>
      <label className="mb-2 block">Bloom
        <input className="w-full" type="range" min={0} max={2} step={0.01} value={bloom} onChange={(e) => setBloom(Number(e.target.value))} />
      </label>
      <label className="mb-2 block">Fog
        <input className="w-full" type="range" min={0} max={1} step={0.01} value={fog} onChange={(e) => setFog(Number(e.target.value))} />
      </label>
      <label className="mb-1 block">Particles
        <input className="w-full" type="range" min={200} max={3000} step={50} value={particles} onChange={(e) => setParticles(Number(e.target.value))} />
      </label>
    </aside>
  );
}
