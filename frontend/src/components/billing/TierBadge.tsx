/**
 * Tier badge — a compact Free/Pro pill for the AppShell header.
 *
 * Reads `GET /api/v1/billing/status` via `useBillingStatus`. Design rules:
 *   - While the status is loading, render NOTHING (no layout flicker / no skeleton
 *     in the dense header bar).
 *   - A paid tier ("pro", "suite", anything not "free") shows a gradient-brand
 *     pill with a crown — the upgraded state should feel earned.
 *   - The free tier shows a quiet outline pill so the badge still anchors the
 *     account area without nagging.
 *   - The pill links to /settings (the Billing & Plan card lives there), matching
 *     how the rest of the header chrome routes.
 *
 * It does NOT gate on `configured`: even when billing is unconfigured the backend
 * reports `tier: "free"`, and showing a neutral "Free" chip is harmless. The
 * upgrade affordance (which DOES depend on `configured`) lives in UpgradeButton.
 */
import { Link } from "@tanstack/react-router";
import { Crown } from "lucide-react";
import { useBillingStatus } from "@/hooks/useBilling";
import { cn } from "@/lib/utils";

/** Title-case an Ops-Center tier code for display ("pro" -> "Pro"). */
function tierLabel(tier: string): string {
  if (!tier) return "Free";
  return tier.charAt(0).toUpperCase() + tier.slice(1).toLowerCase();
}

export function TierBadge({ className }: { className?: string }) {
  const { data, isLoading } = useBillingStatus();

  // Render nothing until we know the tier — avoids a flash of "Free" that then
  // flips to "Pro" for paying users on every page load.
  if (isLoading || !data) return null;

  const isPaid = data.tier.toLowerCase() !== "free";
  const label = tierLabel(data.tier);

  return (
    <Link
      to="/settings"
      aria-label={`Plan: ${label}. Open billing settings.`}
      title={`${label} plan`}
      className={cn(
        "focus-ring inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-2.5 text-xs font-semibold transition-colors",
        isPaid
          ? "bg-gradient-brand text-primary-foreground shadow-[var(--shadow-1)] hover:opacity-90"
          : "border border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground",
        className
      )}
    >
      {isPaid ? <Crown className="h-3.5 w-3.5 shrink-0" strokeWidth={2} /> : null}
      {label}
    </Link>
  );
}
