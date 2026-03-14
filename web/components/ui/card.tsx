import * as React from "react";

import { cn } from "@/lib/cn";

export function Card({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-2xl bg-white/70 p-6 shadow-sm ring-1 ring-black/10 backdrop-blur",
        className
      )}
      {...props}
    />
  );
}

