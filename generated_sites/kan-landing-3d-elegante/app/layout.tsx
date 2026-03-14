import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "KAN | Arquitectura IA para negocio real",
  description:
    "Automatizaciones premium con chatbots, agentes y flujos para ventas, soporte y operación 24/7.",
  openGraph: {
    title: "KAN | Arquitectura IA para negocio real",
    description:
      "Empleados digitales 24/7: automatiza ventas, soporte, agenda y operación con una experiencia premium.",
    type: "website",
    locale: "es_MX",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es-MX">
      <body>{children}</body>
    </html>
  );
}
