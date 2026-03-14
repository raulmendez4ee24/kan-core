# Landing 3D Elegante para Automatizaciones (KAN)

Landing de ventas premium (es-MX) para automatizaciones: chatbots, agentes, flujos y panel.

## Stack
- Next.js (App Router) + TypeScript
- TailwindCSS
- Framer Motion
- Spline Viewer (web component)

## Correr local
1. `npm install`
2. `npm run dev`
3. Abre `http://localhost:3000`

## Componentes clave
- `components/sections/Hero.tsx`
- `components/sections/SplineRobot.tsx`
- `components/sections/Pricing.tsx`
- `components/sections/DashboardPreview.tsx`
- `components/sections/LeadForm.tsx`

## Spline (robot 3D)
Archivo: `components/sections/SplineRobot.tsx`

URLs propuestas (robot elegante, sin look neón):
1. **Default**: `https://prod.spline.design/HqdfCmOueigtautT/scene.splinecode`
2. Alternativa 1: `https://prod.spline.design/UWoeqiir20o49Dah/scene.splinecode`
3. Alternativa 2: `https://prod.spline.design/U9O6K7fXziMEU7Wu/scene.splinecode`

Cambia la constante `SPLINE_URL_DEFAULT` y deja las otras como respaldo.

## Fallback / performance
- `SplineRobot` carga script de forma lazy con `next/script`.
- Si hay `prefers-reduced-motion` o dispositivo low-end, muestra fallback estático (`public/robot-fallback.svg`).
- Para forzar fallback 2D manualmente (por ejemplo QA móvil), crea `.env.local` con:
  - `NEXT_PUBLIC_FORCE_SPLINE_FALLBACK=true`
- El contenedor 3D tiene tamaño fijo para evitar layout shift.

## SEO
- Metadata y OG: `app/layout.tsx`
- `app/sitemap.ts`
- `app/robots.ts`

## Checklist deploy (Vercel)
1. Importar repo/proyecto en Vercel.
2. Build command: `npm run build`.
3. Revisar `sitemap` y dominio real en `robots/sitemap`.
4. Validar Lighthouse móvil (objetivo 85+).
5. Verificar fallback visual en móviles low-end.

## Licencias / atribuciones
- Revisa licencia de la escena Spline antes de uso comercial.
- Si requiere atribución, agregar en footer y en este README.
- Reemplazar assets con restricción comercial por assets permitidos.
