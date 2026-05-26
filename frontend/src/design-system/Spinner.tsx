import { Loader2 } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";

export interface SpinnerProps {
  label?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const sizeClasses = {
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
  lg: "h-5 w-5"
} as const;

export function Spinner({ label = "Fetching", size = "md", className }: SpinnerProps) {
  const reduce = useReducedMotion();

  return (
    <span className={cn("inline-flex items-center gap-2 text-sm text-muted-foreground", className)} role="status">
      <motion.span
        animate={reduce ? undefined : { rotate: 360 }}
        transition={reduce ? undefined : { duration: 0.9, repeat: Infinity, ease: "linear" }}
        aria-hidden="true"
      >
        <Loader2 className={cn(sizeClasses[size], "text-current")} strokeWidth={1.6} />
      </motion.span>
      {label ? <span>{label}</span> : null}
    </span>
  );
}
