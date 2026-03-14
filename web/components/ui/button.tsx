"use client";

import * as React from "react";

import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  className,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  const base =
    "inline-flex items-center justify-center whitespace-nowrap rounded-xl px-4 py-2 text-sm font-medium transition " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink-500 focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none";

  const variants: Record<ButtonVariant, string> = {
    primary:
      "bg-ink-900 text-white shadow-sm hover:bg-ink-800 active:bg-ink-900",
    secondary:
      "bg-white/70 text-ink-900 shadow-sm ring-1 ring-black/10 hover:bg-white active:bg-white",
    ghost:
      "bg-transparent text-ink-900 hover:bg-black/5 active:bg-black/10",
    danger:
      "bg-red-600 text-white shadow-sm hover:bg-red-500 active:bg-red-600"
  };

  return <button className={cn(base, variants[variant], className)} {...props} />;
}

