import { useEffect, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

export interface KeyboardHintProps {
  keys: string | string[];
  label?: string;
  disabled?: boolean;
  className?: string;
}

function isMacPlatform() {
  if (typeof navigator === "undefined") return true;
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform);
}

function normalizeKey(key: string, mac: boolean) {
  const value = key.trim().toLowerCase();
  if (value === "cmd" || value === "command" || value === "meta") return mac ? "⌘" : "Ctrl";
  if (value === "mod") return mac ? "⌘" : "Ctrl";
  if (value === "opt" || value === "option" || value === "alt") return mac ? "⌥" : "Alt";
  if (value === "shift") return mac ? "⇧" : "Shift";
  if (value === "ctrl" || value === "control") return mac ? "⌃" : "Ctrl";
  if (value === "enter" || value === "return") return "↵";
  if (value === "esc" || value === "escape") return "Esc";
  return key.length === 1 ? key.toUpperCase() : key;
}

export function KeyboardHint({ keys, label, disabled = false, className }: KeyboardHintProps) {
  const [mac, setMac] = useState(true);

  useEffect(() => {
    setMac(isMacPlatform());
  }, []);

  const keyList = useMemo(() => (Array.isArray(keys) ? keys : keys.split("+")), [keys]);
  const display = keyList.map((key) => normalizeKey(key, mac));

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-[var(--radius-sm)] border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] font-medium leading-none text-muted-foreground shadow-[inset_0_-1px_0_oklch(var(--border)_/_0.65)]",
        disabled && "opacity-45",
        className
      )}
      title={label}
      aria-label={label ?? display.join(" plus ")}
    >
      {display.map((key, index) => (
        <span key={`${key}-${index}`} className="inline-flex items-center">
          {index > 0 ? <span className="px-0.5 text-muted-foreground/60">+</span> : null}
          <span>{key}</span>
        </span>
      ))}
    </span>
  );
}
