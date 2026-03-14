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
