"use client";
import { create } from "zustand";

export type HeroVariant = "A" | "B" | "C";
export type QualityTier = "ULTRA" | "HIGH" | "MEDIUM" | "LOW";

type SceneState = {
  panelOpen: boolean;
  hero: HeroVariant;
  quality: QualityTier;
  bloom: number;
  fog: number;
  particles: number;
  setPanelOpen: (v: boolean) => void;
  setHero: (v: HeroVariant) => void;
  setQuality: (v: QualityTier) => void;
  setBloom: (v: number) => void;
  setFog: (v: number) => void;
  setParticles: (v: number) => void;
};

export const useSceneStore = create<SceneState>((set) => ({
  panelOpen: false,
  hero: "A",
  quality: "HIGH",
  bloom: 0.8,
  fog: 0.2,
  particles: 1200,
  setPanelOpen: (v) => set({ panelOpen: v }),
  setHero: (v) => set({ hero: v }),
  setQuality: (v) => set({ quality: v }),
  setBloom: (v) => set({ bloom: v }),
  setFog: (v) => set({ fog: v }),
  setParticles: (v) => set({ particles: v }),
}));
