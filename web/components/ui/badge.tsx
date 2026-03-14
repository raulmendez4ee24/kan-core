import * as React from "react";

import { cn } from "@/lib/cn";

export function Badge({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full bg-black/5 px-2.5 py-1 text-xs font-medium text-ink-900 ring-1 ring-black/10",
        className
      )}
      {...props}
    />
  );
}

