import type { Metadata } from "next";
import { IBM_Plex_Mono, Space_Grotesk } from "next/font/google";
import "./globals.css";

const grotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-grotesk",
  display: "swap"
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap"
});

export const metadata: Metadata = {
  title: "KAN Console",
  description: "Task Studio + Proof-of-Work"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={`${grotesk.variable} ${mono.variable}`}>
      <body className="min-h-screen">
        <div className="mx-auto w-full max-w-6xl px-5 py-10">{children}</div>
      </body>
    </html>
  );
}

