/**
 * Billing & Plan — Settings card.
 *
 * The full billing surface (the header only carries the compact TierBadge).
 * Mirrors the other Settings cards (CardDAV, PATs) in shape and tokens:
 *   - Card header with an icon + title + description.
 *   - Current-plan summary row with a Free/Pro pill and (when present) the
 *     subscription status.
 *   - Action: UpgradeButton, which self-selects between "Upgrade" (free +
 *     configured) and "Manage subscription" (paid + portal_url), and hides
 *     itself when billing is unconfigured.
 *   - An inert hint line when billing isn't wired up, so the card never looks
 *     broken — it just explains there's nothing to do yet.
 *
 * Reads `GET /api/v1/billing/status` via `useBillingStatus`. INERT-safe: the
 * backend reports `tier: "free", configured: false` when Ops-Center is unset.
 */
import { CreditCard } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { UpgradeButton } from "@/components/billing/UpgradeButton";
import { useBillingStatus } from "@/hooks/useBilling";

function tierLabel(tier: string): string {
  if (!tier) return "Free";
  return tier.charAt(0).toUpperCase() + tier.slice(1).toLowerCase();
}

export function BillingSection() {
  const { data, isLoading } = useBillingStatus();

  const isPaid = (data?.tier ?? "free").toLowerCase() !== "free";
  const label = tierLabel(data?.tier ?? "free");

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <CreditCard className="h-4 w-4 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
            Billing &amp; Plan
          </CardTitle>
          <CardDescription>
            Your subscription tier. Upgrades and payment are handled securely by Magic Unicorn
            billing.
          </CardDescription>
        </div>
        {/* Self-hides when unconfigured; becomes "Manage subscription" for Pro. */}
        <UpgradeButton label="Upgrade to Pro" />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-12 w-full rounded-md" />
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-foreground">Current plan</span>
              <Badge variant={isPaid ? "success" : "outline"} className="rounded-full">
                {label}
              </Badge>
              {data?.status ? (
                <span className="text-xs text-muted-foreground">{data.status}</span>
              ) : null}
            </div>
            {data && !data.configured ? (
              <p className="text-xs text-muted-foreground">
                Paid plans aren&apos;t available yet — you&apos;re on the free tier.
              </p>
            ) : !isPaid ? (
              <p className="text-xs text-muted-foreground">
                Upgrade to unlock cloud sync and higher limits.
              </p>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
