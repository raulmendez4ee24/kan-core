"use client";

import * as React from "react";

import { cn } from "@/lib/cn";

export function Input({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-xl bg-white/70 px-3 text-sm ring-1 ring-black/10 placeholder:text-black/40 " +
          "focus:outline-none focus:ring-2 focus:ring-ink-500 focus:ring-offset-2",
        className
      )}
      {...props}
    />
  );
}

