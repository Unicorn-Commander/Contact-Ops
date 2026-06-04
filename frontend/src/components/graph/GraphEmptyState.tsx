/**
 * Graph empty state — shown ONLY when the tenant has zero contacts.
 *
 * The relationship graph has nothing to draw until people exist, so instead of
 * a bare "paste a UUID" box we reuse the dashboard's `OnboardingState` import
 * CTAs (Apple / Microsoft 365 / Gmail) with graph-flavored framing. The import
 * affordances route exactly like the Dashboard's empty state: source cards →
 * /settings (where the import lives), "add a person" → /people, "create an
 * organization" → /orgs.
 *
 * This is deliberately distinct from the "data exists, no root selected yet"
 * state (the page roots a default / shows a search-first invite for that) —
 * here there is genuinely nothing in the tenant.
 */
import { useNavigate } from "@tanstack/react-router";
import { OnboardingState } from "@/components/dashboard/OnboardingState";

export function GraphEmptyState() {
  const navigate = useNavigate();
  return (
    // Mirror the canvas empty/loading layout: clear the absolute left config
    // panel on md+ with the same pl offset the GraphCanvas placeholders use.
    <div className="flex h-full min-h-[520px] w-full items-start justify-center overflow-y-auto px-4 py-10 md:pl-[33rem]">
      <div className="w-full max-w-2xl">
        <div className="mb-5 text-center md:text-left">
          <h1 className="text-xl font-semibold leading-7 tracking-tight">Your relationship graph</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Import your contacts to see how everyone connects — people, organizations, and the relationships
            between them, rendered as a living network.
          </p>
        </div>
        <OnboardingState
          onImport={() => void navigate({ to: "/settings" })}
          onAddPerson={() => void navigate({ to: "/people" })}
          onAddOrg={() => void navigate({ to: "/orgs" })}
        />
      </div>
    </div>
  );
}
