import { cn } from "@/lib/utils";

export interface StreamingProps {
  text: string;
  complete?: boolean;
  label?: string;
  className?: string;
}

export function Streaming({ text, complete = false, label = "Agent output", className }: StreamingProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm text-muted-foreground", className)} aria-label={label}>
      <span>{text}</span>
      {!complete ? (
        <span
          className="co-streaming-cursor inline-block h-4 w-[2px] rounded-full bg-[oklch(var(--primary))]"
          aria-hidden="true"
        />
      ) : null}
    </span>
  );
}
