import type { CSSProperties } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";
import { agentColorVars } from "@/design-system/tokens";

export interface PulseProps {
  agentSlug?: string;
  label?: string;
  className?: string;
}

export function Pulse({ agentSlug = "agent", label = "Agent thinking", className }: PulseProps) {
  const reduce = useReducedMotion();
  const style = agentColorVars(agentSlug) as CSSProperties;

  return (
    <span className={cn("inline-flex items-center gap-2 text-sm text-muted-foreground", className)} style={style} role="status">
      <span className="inline-flex h-5 items-center gap-1" aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <motion.span
            key={index}
            className="h-1.5 w-1.5 rounded-full bg-[oklch(0.68_0.15_var(--agent-hue))]"
            animate={reduce ? undefined : { opacity: [0.35, 1, 0.35], scale: [0.85, 1.15, 0.85] }}
            transition={
              reduce
                ? undefined
                : {
                    duration: 1.05,
                    repeat: Infinity,
                    delay: index * 0.12,
                    ease: "easeInOut"
                  }
            }
          />
        ))}
      </span>
      <span>{label}</span>
    </span>
  );
}
