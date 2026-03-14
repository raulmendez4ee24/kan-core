"use client";

import Image from "next/image";
import Script from "next/script";
import { useEffect, useMemo, useRef, useState } from "react";

type WindowWithSpline = Window & {
  customElements: CustomElementRegistry;
};

type SplineRobotProps = {
  className?: string;
};

const SPLINE_URL_DEFAULT =
  "https://prod.spline.design/HqdfCmOueigtautT/scene.splinecode";
// Alternative 1 (cambiar si deseas): https://prod.spline.design/UWoeqiir20o49Dah/scene.splinecode
// Alternative 2 (cambiar si deseas): https://prod.spline.design/U9O6K7fXziMEU7Wu/scene.splinecode
const FORCE_FALLBACK = process.env.NEXT_PUBLIC_FORCE_SPLINE_FALLBACK === "true";

export default function SplineRobot({ className }: SplineRobotProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [shouldLoad, setShouldLoad] = useState(false);
  const [scriptLoaded, setScriptLoaded] = useState(false);
  const [isLowPower, setIsLowPower] = useState(false);
  const [urlReady, setUrlReady] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const lowCore = (navigator.hardwareConcurrency || 8) <= 4;
    const lowMemory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory
      ? ((navigator as Navigator & { deviceMemory?: number }).deviceMemory || 8) <= 4
      : false;
    setIsLowPower(media.matches || lowCore || lowMemory);

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setShouldLoad(true);
          observer.disconnect();
        }
      },
      { threshold: 0.22 }
    );
    if (hostRef.current) observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let active = true;
    const checkUrl = async () => {
      try {
        const res = await fetch(SPLINE_URL_DEFAULT, { method: "HEAD" });
        if (active) setUrlReady(res.ok);
      } catch {
        if (active) setUrlReady(false);
      }
    };
    checkUrl();
    return () => {
      active = false;
    };
  }, []);

  const showSpline = useMemo(
    () => shouldLoad && scriptLoaded && !isLowPower && !FORCE_FALLBACK && urlReady,
    [shouldLoad, scriptLoaded, isLowPower, urlReady]
  );

  return (
    <div ref={hostRef} className={className}>
      {shouldLoad ? (
        <Script
          src="https://unpkg.com/@splinetool/viewer@1.12.58/build/spline-viewer.js"
          type="module"
          strategy="lazyOnload"
          onLoad={() => {
            const win = window as WindowWithSpline;
            if (win.customElements) setScriptLoaded(true);
          }}
        />
      ) : null}

      <div className="relative h-full w-full overflow-hidden rounded-2xl border border-black/10 bg-white/70">
        {showSpline ? (
          <spline-viewer url={SPLINE_URL_DEFAULT} style={{ width: "100%", height: "100%" }}></spline-viewer>
        ) : (
          <Image
            src="/robot-fallback.svg"
            alt="Robot asistente premium"
            fill
            className="object-cover"
            priority
          />
        )}
      </div>
    </div>
  );
}
