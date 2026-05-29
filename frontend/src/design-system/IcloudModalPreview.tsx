/**
 * IcloudModalPreview — auth-free showcase mounting the *real*
 * IcloudConnectModal so screenshots show the actual disclosure component
 * (with its 7-step list + amber 2FA callout) and the decodeConnectorError
 * banner.
 *
 * The ConnectorsPreview mocks the modal for grid/state coverage; this
 * companion preview drives the live component so we can capture:
 *   - Disclosure closed (default open state of the modal)
 *   - Disclosure open (clicked once on mount via a `?open` query param)
 *   - Error banner from `decodeConnectorError({ code: "INVALID_CREDENTIALS" })`
 *     (driven via `?error=1`)
 *
 * Variants in the harness are selected via `?variant=closed|open|error`. The
 * screenshot script flips this param so each capture targets exactly the
 * state we need.
 */
import { useEffect, useMemo, useState } from "react";
import { IcloudConnectModal } from "@/components/connectors/IcloudConnectModal";
import { decodeConnectorError } from "@/components/connectors/decodeConnectorError";
import { cn } from "@/lib/utils";
import type { ThemeMode } from "@/design-system/tokens";

type ModalVariant = "closed" | "open" | "error";

function useQueryVariant(): { variant: ModalVariant; theme: ThemeMode } {
  return useMemo(() => {
    if (typeof window === "undefined") return { variant: "closed", theme: "dark" };
    const params = new URLSearchParams(window.location.search);
    const v = params.get("variant");
    const t = params.get("theme");
    return {
      variant: v === "open" || v === "error" ? v : "closed",
      theme: t === "light" ? "light" : "dark"
    };
  }, []);
}

function ModalFrame({
  variant,
  theme
}: {
  variant: ModalVariant;
  theme: ThemeMode;
}) {
  const [open, setOpen] = useState(true);

  // For the "open" variant we expand the <details> disclosure programmatically
  // after the modal mounts. The native open attribute on <details> is the
  // simplest path here.
  useEffect(() => {
    if (variant !== "open") return;
    const timer = window.setTimeout(() => {
      const details = document.querySelector("details.co-disclosure");
      if (details && !details.hasAttribute("open")) {
        details.setAttribute("open", "");
      }
    }, 50);
    return () => window.clearTimeout(timer);
  }, [variant]);

  // For the "error" variant we feed the modal the friendly INVALID_CREDENTIALS
  // copy that decodeConnectorError emits.
  const errorMessage =
    variant === "error"
      ? decodeConnectorError({ code: "INVALID_CREDENTIALS" }, { provider: "icloud" })
      : null;

  return (
    <div
      data-theme={theme}
      className={cn(
        "relative isolate min-h-[800px] overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
      )}
    >
      <IcloudConnectModal
        open={open}
        onOpenChange={setOpen}
        onSubmit={() => undefined}
        errorMessage={errorMessage}
      />
    </div>
  );
}

export function IcloudModalPreview() {
  const { variant, theme } = useQueryVariant();
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">iCloud Connect modal</h2>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
          The real <code className="font-mono">IcloudConnectModal</code> mounted so the disclosure,
          numbered steps, and decoded error banner are screenshottable without auth.
          Switch state via{" "}
          <code className="font-mono">?variant=closed|open|error</code> and theme via{" "}
          <code className="font-mono">?theme=dark|light</code>.
        </p>
      </div>
      <ModalFrame variant={variant} theme={theme} />
    </section>
  );
}

export default IcloudModalPreview;
