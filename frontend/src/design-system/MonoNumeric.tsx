import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface MonoNumericProps {
  children: ReactNode;
  tone?: "default" | "muted" | "strong";
  className?: string;
  title?: string;
}

const toneClasses = {
  default: "text-foreground",
  muted: "text-muted-foreground",
  strong: "text-foreground font-semibold"
} as const;

export function MonoNumeric({ children, tone = "default", className, title }: MonoNumericProps) {
  return (
    <span className={cn("co-mono-numeric font-mono lining-nums", toneClasses[tone], className)} title={title}>
      {children}
    </span>
  );
}
