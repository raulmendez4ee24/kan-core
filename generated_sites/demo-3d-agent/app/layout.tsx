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
