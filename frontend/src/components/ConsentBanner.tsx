/**
 * Opt-in analytics consent banner — shown once, only when the user hasn't yet
 * decided AND at least one analytics tracker is actually configured (otherwise
 * there's nothing to consent to, so we don't nag). Granular control lives in
 * Settings → Privacy & Analytics.
 */
import { Button } from "@/components/ui/button";
import { useConsent } from "@/lib/consent";
import { env } from "@/lib/env";

function anyAnalyticsConfigured(): boolean {
  return Boolean(
    (env.umamiSrc && env.umamiWebsiteId) || (env.posthogKey && env.posthogHost)
  );
}

export function ConsentBanner() {
  const { consent, setConsent } = useConsent();

  if (consent.decided || !anyAnalyticsConfigured()) return null;

  return (
    <div
      role="dialog"
      aria-label="Analytics consent"
      className="fixed inset-x-0 bottom-0 z-50 border-t border-border bg-background/95 p-4 backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <div className="mx-auto flex max-w-4xl flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">
          We'd like to use privacy-friendly analytics to improve Contact-Ops. Nothing is
          tracked until you accept, and we never capture your contact data.{" "}
          <a href="/legal/privacy.html" className="text-link hover:underline">
            Privacy
          </a>
          . You can change this anytime in Settings.
        </p>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConsent({ web: false, product: false })}
          >
            Reject
          </Button>
          <Button
            size="sm"
            variant="gradient"
            onClick={() => setConsent({ web: true, product: true })}
          >
            Accept analytics
          </Button>
        </div>
      </div>
    </div>
  );
}
