/**
 * Upgrade / Manage-subscription affordance (Phase: billing-ops-center).
 *
 * One component, two states driven by `GET /api/v1/billing/status`:
 *
 *   - PAID tier  -> "Manage subscription" → opens `portal_url` (Stripe customer
 *     portal). If Ops-Center sent no `portal_url`, the manage button is hidden
 *     (nothing to link to) — we never render a dead button.
 *   - FREE tier  -> "Upgrade" → POST /api/v1/billing/checkout → redirect the
 *     browser to the returned Stripe URL.
 *
 * Graceful degradation (load-bearing): when billing is UNCONFIGURED
 * (`configured: false`, or checkout 503s) there is nothing to buy, so the
 * upgrade CTA is hidden entirely. A late 503 (race) is caught and disables the
 * button rather than throwing.
 *
 * The component renders NOTHING while status is loading or unknown, so it can be
 * dropped into a header or a Settings card without reserving flickery space.
 */
import { useState } from "react";
import { ArrowUpCircle, ExternalLink, Loader2 } from "lucide-react";
import { Button, type ButtonProps } from "@/components/ui/button";
import { useBillingStatus, useCheckoutMutation } from "@/hooks/useBilling";
import { BillingUnconfiguredError } from "@/lib/billing";
import { cn } from "@/lib/utils";

type UpgradeButtonProps = {
  /**
   * Ops-Center plan code to check out. Defaults to "pro" — the proxy forwards
   * whatever string this is as `plan_code`, so a richer plan picker can pass a
   * code resolved from `GET /api/v1/billing/plans`.
   */
  planCode?: string;
  /** Visual variant for the upgrade CTA. Defaults to the brand gradient. */
  variant?: ButtonProps["variant"];
  size?: ButtonProps["size"];
  className?: string;
  /** Override the upgrade label (e.g. "Upgrade to Pro"). */
  label?: string;
};

export function UpgradeButton({
  planCode = "pro",
  variant = "gradient",
  size = "sm",
  className,
  label = "Upgrade"
}: UpgradeButtonProps) {
  const { data, isLoading } = useBillingStatus();
  const checkout = useCheckoutMutation();
  // A late 503 (billing went unconfigured between status read and click) flips
  // this so we stop offering an action that can't complete.
  const [unconfigured, setUnconfigured] = useState(false);

  // Unknown / loading -> render nothing (no flicker in headers or cards).
  if (isLoading || !data) return null;

  const isPaid = data.tier.toLowerCase() !== "free";

  // ---- Paid: Manage subscription (only when there's a portal to open) ----
  if (isPaid) {
    if (!data.portal_url) return null;
    return (
      <Button
        variant={variant === "gradient" ? "outline" : variant}
        size={size}
        className={className}
        onClick={() => window.open(data.portal_url ?? "", "_blank", "noopener,noreferrer")}
        aria-label="Manage subscription in the billing portal"
      >
        <ExternalLink className="h-4 w-4" strokeWidth={1.8} />
        Manage subscription
      </Button>
    );
  }

  // ---- Free: hide the CTA entirely when billing isn't wired up ----
  if (!data.configured || unconfigured) return null;

  const handleUpgrade = async () => {
    try {
      const { checkout_url } = await checkout.mutateAsync({ plan_code: planCode });
      // Full-page redirect to Stripe — checkout must own the whole window.
      window.location.assign(checkout_url);
    } catch (err) {
      // Billing flipped to unconfigured mid-flight: stop offering the action.
      if (err instanceof BillingUnconfiguredError) setUnconfigured(true);
      // Every other error already toasted in the hook; nothing to do here.
    }
  };

  const pending = checkout.isPending;
  return (
    <Button
      variant={variant}
      size={size}
      className={cn(className)}
      disabled={pending}
      onClick={() => void handleUpgrade()}
      aria-label="Upgrade your plan"
    >
      {pending ? (
        <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.8} />
      ) : (
        <ArrowUpCircle className="h-4 w-4" strokeWidth={1.8} />
      )}
      {pending ? "Starting checkout…" : label}
    </Button>
  );
}
