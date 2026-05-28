/**
 * A richer pending-proposal row for the dashboard.
 *
 * Entity icon → name + action → agent attribution (gradient sigil + slug) →
 * confidence bar. On hover/focus a pair of quick-action buttons (Approve /
 * Reject) reveal on the right; the confidence bar slides out of the way. When
 * `onApprove`/`onReject` are omitted the buttons render as a visual affordance
 * only (used by the auth-free preview). Keyboard users get the actions via
 * `focus-within` so they're never hidden behind hover.
 */
import { Building2, Check, Users, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AgentSigil } from "./AgentSigil";
import { ConfidenceBar } from "./ConfidenceBar";

export interface ProposalRowProps {
  entityKind: "person" | "org";
  entityName: string;
  /** Human action label, e.g. "merge duplicate" / "update domain". */
  action: string;
  agentSlug: string;
  /** 0..1 confidence. */
  confidence: number;
  /** Navigates into the full review surface (e.g. clicking the row body). */
  onOpen?: () => void;
  onApprove?: () => void;
  onReject?: () => void;
  /** Disables the quick-actions + dims the row (optimistic in-flight). */
  pending?: boolean;
  className?: string;
}

export function ProposalRow({
  entityKind,
  entityName,
  action,
  agentSlug,
  confidence,
  onOpen,
  onApprove,
  onReject,
  pending = false,
  className
}: ProposalRowProps) {
  const EntityIcon = entityKind === "org" ? Building2 : Users;
  const interactive = Boolean(onApprove || onReject);

  return (
    <div
      className={cn(
        "group/row relative -mx-2 flex items-center gap-3 rounded-md px-2 py-2.5 transition-colors hover:bg-muted",
        pending && "pointer-events-none opacity-50",
        className
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        className="focus-ring -my-2.5 flex min-w-0 flex-1 items-center gap-3 rounded-md py-2.5 text-left"
        aria-label={`Open proposal for ${entityName}`}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <EntityIcon className="h-4 w-4" strokeWidth={1.8} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{entityName}</span>
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
            <span className="shrink-0 capitalize">{action}</span>
            <span className="shrink-0 text-border">·</span>
            <AgentSigil slug={agentSlug} />
          </span>
        </span>
      </button>

      {/* Confidence — fades out as the quick-actions fade in (hover/focus). */}
      <ConfidenceBar
        value={confidence}
        className={cn(
          "shrink-0 transition-opacity duration-150",
          interactive &&
            "group-hover/row:pointer-events-none group-hover/row:opacity-0 group-focus-within/row:pointer-events-none group-focus-within/row:opacity-0"
        )}
      />

      {interactive ? (
        <div
          className={cn(
            "pointer-events-none absolute right-2 flex items-center gap-1 opacity-0 transition-opacity duration-150",
            "group-hover/row:pointer-events-auto group-hover/row:opacity-100",
            "group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100"
          )}
        >
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-7 w-7 text-[oklch(var(--co-emerald-500))] hover:bg-[oklch(var(--co-emerald-500)/0.16)] hover:text-[oklch(var(--co-emerald-500))]"
            onClick={onApprove}
            disabled={pending}
            aria-label={`Approve proposal for ${entityName}`}
            title="Approve"
          >
            <Check className="h-4 w-4" strokeWidth={2} />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-7 w-7 text-[oklch(var(--co-rose-500))] hover:bg-[oklch(var(--co-rose-500)/0.16)] hover:text-[oklch(var(--co-rose-500))]"
            onClick={onReject}
            disabled={pending}
            aria-label={`Reject proposal for ${entityName}`}
            title="Reject"
          >
            <X className="h-4 w-4" strokeWidth={2} />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
