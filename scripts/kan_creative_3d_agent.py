#!/usr/bin/env python3
import argparse
from pathlib import Path
from textwrap import dedent


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a production-ready 3D web project scaffold.")
    parser.add_argument("--name", default="kan-creative-3d-site", help="Project folder name")
    parser.add_argument("--default-hero", choices=["A", "B", "C"], default="A")
    parser.add_argument("--out", default="generated_sites", help="Base output directory")
    args = parser.parse_args()

    root = Path(args.out).resolve() / args.name
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"Target folder is not empty: {root}")

    package_json = dedent(
        """\
        {
          "name": "kan-creative-3d-site",
          "private": true,
          "version": "0.1.0",
          "scripts": {
            "dev": "next dev",
            "build": "next build",
            "start": "next start"
          },
          "dependencies": {
            "@react-three/drei": "^9.121.4",
            "@react-three/fiber": "^8.17.10",
            "@react-three/postprocessing": "^2.16.2",
            "framer-motion": "^12.4.1",
            "gsap": "^3.12.5",
            "next": "^14.2.15",
            "postprocessing": "^6.36.6",
            "react": "^18.2.0",
            "react-dom": "^18.2.0",
            "three": "^0.172.0",
            "zustand": "^5.0.2"
          },
          "devDependencies": {
            "@types/node": "^20.17.12",
            "@types/react": "^18.3.12",
            "@types/react-dom": "^18.3.1",
            "autoprefixer": "^10.4.20",
            "postcss": "^8.4.49",
            "tailwindcss": "^3.4.16",
            "typescript": "^5.7.2"
          }
        }
        """
    )

    write(root / "package.json", package_json)
    write(
        root / "next.config.mjs",
        dedent(
            """\
            /** @type {import('next').NextConfig} */
            const nextConfig = {
              reactStrictMode: true,
            };
            export default nextConfig;
            """
        ),
    )
    write(
        root / "tsconfig.json",
        dedent(
            """\
            {
              "compilerOptions": {
                "target": "ES2017",
                "lib": ["dom", "dom.iterable", "esnext"],
                "allowJs": false,
                "skipLibCheck": true,
                "strict": true,
                "noEmit": true,
                "esModuleInterop": true,
                "module": "esnext",
                "moduleResolution": "bundler",
                "resolveJsonModule": true,
                "isolatedModules": true,
                "jsx": "preserve",
                "incremental": true,
                "plugins": [{"name": "next"}]
              },
              "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
              "exclude": ["node_modules"]
            }
            """
        ),
    )
    write(root / "next-env.d.ts", '/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n')
    write(
        root / "postcss.config.cjs",
        "module.exports = { plugins: { tailwindcss: {}, autoprefixer: {} } };",
    )
    write(
        root / "tailwind.config.ts",
        dedent(
            """\
            import type { Config } from "tailwindcss";

            export default {
              content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
              theme: {
                extend: {
                  colors: {
                    bg: "#06080f",
                    cyan: "#42e8ff",
                    neon: "#9f5bff"
                  }
                }
              },
              plugins: []
            } satisfies Config;
            """
        ),
    )
    write(
        root / "app/globals.css",
        dedent(
            """\
            @tailwind base;
            @tailwind components;
            @tailwind utilities;

            :root { color-scheme: dark; }
            html, body { margin: 0; padding: 0; background: radial-gradient(1200px 800px at 20% 10%, #17213b 0%, #06080f 60%); color: #f4f7ff; }
            * { box-sizing: border-box; }

            .glass {
              background: rgba(13, 20, 37, 0.56);
              border: 1px solid rgba(113, 219, 255, 0.25);
              backdrop-filter: blur(8px);
            }
            """
        ),
    )
    write(
        root / "app/layout.tsx",
        dedent(
            """\
            import "./globals.css";
            import type { Metadata } from "next";

            export const metadata: Metadata = {
              title: "KAN Creative 3D Web Agent Demo",
              description: "AAA sci-fi web generated scaffold",
            };

            export default function RootLayout({ children }: { children: React.ReactNode }) {
              return (
                <html lang="en">
                  <body>{children}</body>
                </html>
              );
            }
            """
        ),
    )
    write(
        root / "components/state/useSceneStore.ts",
        dedent(
            f"""\
            "use client";
            import {{ create }} from "zustand";

            export type HeroVariant = "A" | "B" | "C";
            export type QualityTier = "ULTRA" | "HIGH" | "MEDIUM" | "LOW";

            type SceneState = {{
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
            }};

            export const useSceneStore = create<SceneState>((set) => ({{
              panelOpen: false,
              hero: "{args.default_hero}",
              quality: "HIGH",
              bloom: 0.8,
              fog: 0.2,
              particles: 1200,
              setPanelOpen: (v) => set({{ panelOpen: v }}),
              setHero: (v) => set({{ hero: v }}),
              setQuality: (v) => set({{ quality: v }}),
              setBloom: (v) => set({{ bloom: v }}),
              setFog: (v) => set({{ fog: v }}),
              setParticles: (v) => set({{ particles: v }}),
            }}));
            """
        ),
    )
    write(
        root / "components/hero/Hero3D.tsx",
        dedent(
            """\
            "use client";
            import { Suspense, useMemo, useRef } from "react";
            import { Canvas, useFrame } from "@react-three/fiber";
            import { Environment, OrbitControls, Float } from "@react-three/drei";
            import { EffectComposer, Bloom } from "@react-three/postprocessing";
            import { ShaderMaterial, Vector2 } from "three";
            import { useSceneStore } from "@/components/state/useSceneStore";

            function EnergyCore() {
              return (
                <Float speed={1.4} rotationIntensity={0.4} floatIntensity={0.6}>
                  <mesh>
                    <icosahedronGeometry args={[1, 8]} />
                    <meshStandardMaterial color="#5ad6ff" emissive="#0af" emissiveIntensity={2} metalness={0.5} roughness={0.2} />
                  </mesh>
                </Float>
              );
            }

            function AndroidHead() {
              return (
                <group>
                  <mesh position={[0, 0.2, 0]}>
                    <capsuleGeometry args={[0.55, 1.2, 8, 14]} />
                    <meshStandardMaterial color="#c8d0dc" metalness={0.95} roughness={0.16} />
                  </mesh>
                  <mesh position={[0, 0.25, 0.57]}>
                    <planeGeometry args={[0.7, 0.16]} />
                    <meshStandardMaterial color="#63ebff" emissive="#43dfff" emissiveIntensity={2.4} />
                  </mesh>
                </group>
              );
            }

            function Portal() {
              return (
                <Float speed={1.1} rotationIntensity={0.25} floatIntensity={0.4}>
                  <mesh rotation-x={Math.PI / 2}>
                    <torusGeometry args={[1.2, 0.22, 18, 120]} />
                    <meshStandardMaterial color="#8148ff" emissive="#7f57ff" emissiveIntensity={1.6} metalness={0.72} roughness={0.28} />
                  </mesh>
                </Float>
              );
            }

            function ParticleField() {
              const particles = useSceneStore((s) => s.particles);
              const ref = useRef<any>(null);
              const points = useMemo(() => {
                const out = new Float32Array(particles * 3);
                for (let i = 0; i < particles; i += 1) {
                  const i3 = i * 3;
                  out[i3 + 0] = (Math.random() - 0.5) * 10;
                  out[i3 + 1] = (Math.random() - 0.5) * 6;
                  out[i3 + 2] = (Math.random() - 0.5) * 8;
                }
                return out;
              }, [particles]);

              useFrame(({ clock, pointer }) => {
                if (!ref.current) return;
                ref.current.rotation.y = clock.elapsedTime * 0.03;
                ref.current.position.x = pointer.x * 0.25;
                ref.current.position.y = pointer.y * 0.18;
              });

              return (
                <points ref={ref}>
                  <bufferGeometry>
                    <bufferAttribute attach="attributes-position" count={points.length / 3} array={points} itemSize={3} />
                  </bufferGeometry>
                  <pointsMaterial color="#8ce9ff" size={0.02} sizeAttenuation />
                </points>
              );
            }

            function DistortionPlane() {
              const matRef = useRef<ShaderMaterial | null>(null);
              const uniforms = useMemo(
                () => ({
                  uTime: { value: 0 },
                  uPointer: { value: new Vector2(0, 0) },
                }),
                []
              );
              useFrame(({ clock, pointer }) => {
                if (!matRef.current) return;
                uniforms.uTime.value = clock.elapsedTime;
                uniforms.uPointer.value.set(pointer.x, pointer.y);
              });
              return (
                <mesh position={[0, 0, -2.8]}>
                  <planeGeometry args={[8.5, 5]} />
                  <shaderMaterial
                    ref={matRef}
                    transparent
                    uniforms={uniforms}
                    vertexShader={`
                      varying vec2 vUv;
                      void main() {
                        vUv = uv;
                        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);
                      }
                    `}
                    fragmentShader={`
                      varying vec2 vUv;
                      uniform float uTime;
                      uniform vec2 uPointer;
                      float noise(vec2 p){ return sin(p.x*9.0+uTime*0.7)*sin(p.y*11.0+uTime*0.6); }
                      void main() {
                        vec2 uv = vUv - 0.5;
                        float n = noise(uv*1.8 + uPointer*0.6) * 0.12;
                        float fres = 1.0 - smoothstep(0.1, 0.7, length(uv));
                        vec3 col = mix(vec3(0.05,0.11,0.18), vec3(0.29,0.78,1.0), fres + n);
                        gl_FragColor = vec4(col, 0.32);
                      }
                    `}
                  />
                </mesh>
              );
            }

            export default function Hero3D() {
              const hero = useSceneStore((s) => s.hero);
              const bloom = useSceneStore((s) => s.bloom);
              const quality = useSceneStore((s) => s.quality);
              const dpr = quality === "ULTRA" ? 2 : quality === "HIGH" ? 1.5 : quality === "MEDIUM" ? 1.2 : 1;

              return (
                <Canvas camera={{ position: [0, 0, 4.4], fov: 48 }} dpr={dpr}>
                  <color attach="background" args={["#05070e"]} />
                  <ambientLight intensity={0.4} />
                  <directionalLight position={[2.5, 3, 2]} intensity={2.1} color="#88dcff" />
                  <Suspense fallback={null}>
                    <Environment preset="city" />
                    <DistortionPlane />
                    <ParticleField />
                    {hero === "A" ? <EnergyCore /> : hero === "B" ? <AndroidHead /> : <Portal />}
                  </Suspense>
                  <OrbitControls enablePan={false} enableZoom={false} maxPolarAngle={1.9} minPolarAngle={1.2} />
                  <EffectComposer>
                    <Bloom intensity={bloom} mipmapBlur />
                  </EffectComposer>
                </Canvas>
              );
            }
            """
        ),
    )
    write(
        root / "components/hero/ControlPanel.tsx",
        dedent(
            """\
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
            """
        ),
    )
    write(
        root / "app/page.tsx",
        dedent(
            """\
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
            """
        ),
    )
    write(
        root / "README.md",
        dedent(
            """\
            # KAN Creative 3D Site (Generated)

            Default hero variant: {hero}

            ## Run
            1. `npm install`
            2. `npm run dev`
            3. Open `http://localhost:3000`

            ## Included
            - Hero 3D AAA with variants A/B/C
            - Hidden panel key `P` for bloom/fog/particles/quality
            - Sci-fi premium UI sections
            - Lead capture form
            - Basic performance tiers (ULTRA/HIGH/MEDIUM/LOW)

            ## Variants
            - A: Energy Core + holographic UI
            - B: Android + rim-light
            - C: Dimensional Portal + neural particles

            ## Spline / GLB Upgrade Path
            - Fast prototype with `@splinetool/react-spline`
            - Replace hero mesh with optimized GLB/GLTF + DRACO
            - Keep the same state and panel for quality controls

            ## Asset & License Checklist
            - Verify each 3D model/textures before production use.
            - Add required attributions in footer or this README.
            - Replace non-commercial assets for commercial deployments.

            ## Suggested Sources (Reference Only)
            - Awwwards, FWA, Godly, Land-book, SiteInspire, Behance, Dribbble
            - Active Theory, Lusion, Resn, Instrument, Buck, Jam3
            - threejs examples, webglsamples, GitHub, CodeSandbox
            - Sketchfab, CGTrader, TurboSquid, Poly Pizza, Kenney, Quaternius, AmbientCG, Poly Haven
            """.format(hero=args.default_hero)
        ),
    )
    write(root / "PROMPT.md", Path("prompts/CREATIVE_3D_WEB_AGENT.md").read_text(encoding="utf-8"))

    print(f"Generated project: {root}")
    print("Next steps:")
    print(f"  cd {root}")
    print("  npm install")
    print("  npm run dev")


if __name__ == "__main__":
    main()
