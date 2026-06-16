/**
 * HelpConnectors — /help/connectors deep walkthrough for the three contact
 * sources (iCloud, Microsoft 365, Gmail).
 *
 * Sections (each addressable by URL hash for the per-card "Learn more" link
 * on /connectors and for the in-modal disclosure footer link):
 *   #icloud  · CardDAV via app-specific password
 *   #m365    · OAuth via Microsoft sign-in popup
 *   #gmail   · OAuth via Google sign-in popup
 *
 * Each section follows the same shape: provider chip + colored sigil, an
 * intro paragraph explaining the auth model, numbered step list, and a
 * troubleshooting subsection.
 *
 * Layout is mobile-first single-column up to lg:, then two-column with a
 * sticky table-of-contents on the right. Everything is presentational —
 * no backend calls, no auth context use.
 */
import { useEffect } from "react";
import { Link } from "@tanstack/react-router";
import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Cloud,
  ExternalLink,
  HelpCircle,
  KeyRound,
  LifeBuoy,
  LogIn,
  Mail,
  Sparkles
} from "lucide-react";
import { cn } from "@/lib/utils";
import { env } from "@/lib/env";

const APPLE_HELP_URL = "https://appleid.apple.com/account/manage";

// ---- Section primitives ----------------------------------------------------

interface ProviderSectionDef {
  id: "icloud" | "m365" | "gmail";
  name: string;
  chipTone: "sky" | "amber" | "rose";
  icon: typeof Cloud;
  authIcon: typeof KeyRound;
  authShape: string;
  intro: string;
}

const PROVIDER_SECTIONS: ProviderSectionDef[] = [
  {
    id: "icloud",
    name: "iCloud",
    chipTone: "sky",
    icon: Cloud,
    authIcon: KeyRound,
    authShape: "Apple ID + an app-specific password",
    intro:
      "iCloud Contacts uses CardDAV. Apple doesn't expose a regular OAuth flow, so you generate a one-off 16-character password on appleid.apple.com and paste it into Contact-Ops. The password is stored encrypted at rest and can be revoked from Apple's portal at any time — revoking it cleanly disconnects this connector."
  },
  {
    id: "m365",
    name: "Microsoft 365",
    chipTone: "amber",
    icon: Mail,
    authIcon: LogIn,
    authShape: "Sign in with Microsoft (read-only contacts)",
    intro:
      "Microsoft 365 uses standard OAuth. Clicking Connect opens a popup with Microsoft's sign-in. You'll sign in with your work or personal Microsoft account and tick a single consent checkbox granting Contact-Ops read-only access to your contacts (Contacts.Read). Contact-Ops never sees your password and can't read your mail, calendar, or files."
  },
  {
    id: "gmail",
    name: "Gmail",
    chipTone: "rose",
    icon: Mail,
    authIcon: LogIn,
    authShape: "Sign in with Google (read-only contacts)",
    intro:
      "Gmail uses standard OAuth via Google. Clicking Connect opens a popup with Google's sign-in. You'll grant Contact-Ops read-only access to your Google Contacts (contacts.readonly scope). We never see your password and can't read your mail or calendar. Note that Google Contacts is separate from auto-saved Gmail interactions — if most of your contacts come from sending emails, see the troubleshooting section below."
  }
];

const CHIP_CLASSES: Record<ProviderSectionDef["chipTone"], string> = {
  sky: "bg-[oklch(var(--co-sky-500)/0.14)] text-[oklch(var(--co-sky-500))] border-[oklch(var(--co-sky-500)/0.32)]",
  amber:
    "bg-[oklch(var(--co-amber-500)/0.14)] text-[oklch(var(--co-amber-500))] border-[oklch(var(--co-amber-500)/0.32)]",
  rose: "bg-[oklch(var(--co-rose-500)/0.14)] text-[oklch(var(--co-rose-500))] border-[oklch(var(--co-rose-500)/0.32)]"
};

const SIGIL_CLASSES: Record<ProviderSectionDef["chipTone"], string> = {
  sky: "bg-[oklch(var(--co-sky-500)/0.18)] text-[oklch(var(--co-sky-500))]",
  amber: "bg-[oklch(var(--co-amber-500)/0.18)] text-[oklch(var(--co-amber-500))]",
  rose: "bg-[oklch(var(--co-rose-500)/0.18)] text-[oklch(var(--co-rose-500))]"
};

// ---- Reusable bits ---------------------------------------------------------

function ProviderChip({ def }: { def: ProviderSectionDef }) {
  const Icon = def.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
        CHIP_CLASSES[def.chipTone]
      )}
    >
      <Icon className="h-3 w-3" strokeWidth={1.8} aria-hidden="true" />
      {def.name}
    </span>
  );
}

function StepList({ items }: { items: React.ReactNode[] }) {
  return (
    <ol className="list-decimal space-y-2 pl-5 text-sm leading-relaxed marker:text-muted-foreground">
      {items.map((item, idx) => (
        <li key={idx}>{item}</li>
      ))}
    </ol>
  );
}

interface TroubleshootingItem {
  symptom: React.ReactNode;
  fix: React.ReactNode;
}

function Troubleshooting({ items }: { items: TroubleshootingItem[] }) {
  return (
    <div className="space-y-3">
      <h4 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
        <LifeBuoy
          className="h-4 w-4 text-[oklch(var(--co-amber-500))]"
          strokeWidth={1.8}
          aria-hidden="true"
        />
        Troubleshooting
      </h4>
      <dl className="space-y-2.5 rounded-lg border border-border bg-card/60 p-4">
        {items.map((item, idx) => (
          <div key={idx} className="space-y-1">
            <dt className="text-sm font-medium text-foreground">{item.symptom}</dt>
            <dd className="flex items-start gap-1.5 text-[13px] leading-relaxed text-muted-foreground">
              <ArrowRight
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[oklch(var(--co-emerald-500))]"
                strokeWidth={2}
                aria-hidden="true"
              />
              <span>{item.fix}</span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// ---- Sections --------------------------------------------------------------

function IcloudSection({ def }: { def: ProviderSectionDef }) {
  const SigilIcon = def.icon;
  return (
    <section
      id={def.id}
      className="scroll-mt-20 space-y-5 rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-1)]"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
              SIGIL_CLASSES[def.chipTone]
            )}
            aria-hidden="true"
          >
            <SigilIcon className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-xl font-semibold tracking-tight">Connecting {def.name}</h2>
              <ProviderChip def={def} />
            </div>
            <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <KeyRound
                className="h-3 w-3 text-[oklch(var(--co-sky-500))]"
                strokeWidth={1.8}
                aria-hidden="true"
              />
              {def.authShape}
            </p>
          </div>
        </div>
      </header>

      <p className="text-sm leading-relaxed text-muted-foreground">{def.intro}</p>

      <div className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-amber-500)/0.35)] bg-[oklch(var(--co-amber-500)/0.08)] px-3 py-2.5 text-sm leading-snug text-foreground">
        <AlertCircle
          className="mt-0.5 h-4 w-4 shrink-0 text-[oklch(var(--co-amber-500))]"
          strokeWidth={2}
          aria-hidden="true"
        />
        <span>
          <strong className="font-semibold">Apple requires two-factor authentication</strong>{" "}
          enabled on your Apple ID before you can generate an app-specific password. If 2FA isn't
          enabled, the App-Specific Passwords menu won't appear on your account page.
        </span>
      </div>

      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <CheckCircle2
            className="h-4 w-4 text-[oklch(var(--co-emerald-500))]"
            strokeWidth={1.8}
            aria-hidden="true"
          />
          Steps
        </h3>
        <StepList
          items={[
            <>
              Open{" "}
              <a
                href={APPLE_HELP_URL}
                target="_blank"
                rel="noreferrer noopener"
                className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
              >
                account.apple.com
                <ExternalLink
                  className="ml-0.5 inline-block h-3 w-3"
                  strokeWidth={2}
                  aria-hidden="true"
                />
              </a>
              .
            </>,
            "Sign in with the Apple ID whose contacts you want to pull.",
            <>
              Open <span className="font-medium text-foreground">Sign-In and Security</span> →{" "}
              <span className="font-medium text-foreground">App-Specific Passwords</span>.
            </>,
            <>
              Click <span className="font-medium text-foreground">+ Generate an app-specific
              password</span>.
            </>,
            <>
              Label it <span className="font-mono text-foreground">Contact-Ops</span> (or
              whatever you'll recognize later).
            </>,
            <>
              Apple will show a 16-character password formatted as{" "}
              <span className="font-mono text-foreground">xxxx-xxxx-xxxx-xxxx</span>.{" "}
              <span className="font-medium text-foreground">Copy it now</span> — Apple only
              shows this password once.
            </>,
            <>
              Back on <Link to="/connectors" className="text-link underline-offset-2 hover:underline focus-ring rounded-sm">/connectors</Link>, click{" "}
              <span className="font-medium text-foreground">Connect</span> on the iCloud card
              and paste the password (dashes optional).
            </>
          ]}
        />
      </div>

      <Troubleshooting
        items={[
          {
            symptom: '"App-Specific Passwords menu isn\'t showing"',
            fix: (
              <>
                Enable 2FA on your Apple ID first. Apple gates the App-Specific Passwords menu
                behind two-factor enrollment; once 2FA is on, refresh the account page and the
                menu will appear under <span className="font-medium text-foreground">Sign-In and
                Security</span>.
              </>
            )
          },
          {
            symptom: '"Apple says the credentials are invalid"',
            fix: (
              <>
                Confirm the password was copied from <a
                  href={APPLE_HELP_URL}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
                >
                  appleid.apple.com
                </a> (not your real Apple ID password) and was generated within the last few
                minutes. App-specific passwords expire if unused — re-generate one and paste
                the new value.
              </>
            )
          }
        ]}
      />
    </section>
  );
}

function M365Section({ def }: { def: ProviderSectionDef }) {
  const SigilIcon = def.icon;
  return (
    <section
      id={def.id}
      className="scroll-mt-20 space-y-5 rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-1)]"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
              SIGIL_CLASSES[def.chipTone]
            )}
            aria-hidden="true"
          >
            <SigilIcon className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-xl font-semibold tracking-tight">Connecting {def.name}</h2>
              <ProviderChip def={def} />
            </div>
            <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <LogIn
                className="h-3 w-3 text-[oklch(var(--co-amber-500))]"
                strokeWidth={1.8}
                aria-hidden="true"
              />
              {def.authShape}
            </p>
          </div>
        </div>
      </header>

      <p className="text-sm leading-relaxed text-muted-foreground">{def.intro}</p>

      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <CheckCircle2
            className="h-4 w-4 text-[oklch(var(--co-emerald-500))]"
            strokeWidth={1.8}
            aria-hidden="true"
          />
          Steps
        </h3>
        <StepList
          items={[
            <>
              On <Link to="/connectors" className="text-link underline-offset-2 hover:underline focus-ring rounded-sm">/connectors</Link>, click{" "}
              <span className="font-medium text-foreground">Connect</span> on the Microsoft 365
              card.
            </>,
            "A small popup will open with Microsoft's sign-in page.",
            "Sign in with the Microsoft account whose contacts you want to pull (work, school, or personal).",
            <>
              Microsoft will show a consent screen listing{" "}
              <span className="font-mono text-foreground">Contacts.Read</span> (read your
              contacts). Click <span className="font-medium text-foreground">Accept</span>.
            </>,
            "The popup closes and Contact-Ops lands the first pull as proposals in your Review Queue."
          ]}
        />
      </div>

      <Troubleshooting
        items={[
          {
            symptom: '"Popup blocked"',
            fix: (
              <>
                Allow popups for <span className="font-mono text-foreground">{window.location.host}</span>{" "}
                and retry; or use full-page redirect — the Connect button falls back to it
                automatically after the first block.
              </>
            )
          },
          {
            symptom: '"Microsoft says the redirect URI is invalid"',
            fix: (
              <>
                Contact your admin. The app's redirect URI needs to be{" "}
                <span className="font-mono text-foreground break-all">
                  {`${env.mcpBaseUrl}/api/connectors/m365/oauth/callback`}
                </span>
                . If the value in Azure portal doesn't match exactly, Microsoft rejects the
                callback.
              </>
            )
          },
          {
            symptom: '"Consent screen says you can\'t access"',
            fix: (
              <>
                The Microsoft account doesn't have{" "}
                <span className="font-mono text-foreground">Contacts.Read</span> granted. Your
                admin needs to grant the permission in the Azure portal under the Contact-Ops
                enterprise app.
              </>
            )
          }
        ]}
      />
    </section>
  );
}

function GmailSection({ def }: { def: ProviderSectionDef }) {
  const SigilIcon = def.icon;
  return (
    <section
      id={def.id}
      className="scroll-mt-20 space-y-5 rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-1)]"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
              SIGIL_CLASSES[def.chipTone]
            )}
            aria-hidden="true"
          >
            <SigilIcon className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-xl font-semibold tracking-tight">Connecting {def.name}</h2>
              <ProviderChip def={def} />
            </div>
            <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <LogIn
                className="h-3 w-3 text-[oklch(var(--co-rose-500))]"
                strokeWidth={1.8}
                aria-hidden="true"
              />
              {def.authShape}
            </p>
          </div>
        </div>
      </header>

      <p className="text-sm leading-relaxed text-muted-foreground">{def.intro}</p>

      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <CheckCircle2
            className="h-4 w-4 text-[oklch(var(--co-emerald-500))]"
            strokeWidth={1.8}
            aria-hidden="true"
          />
          Steps
        </h3>
        <StepList
          items={[
            <>
              On <Link to="/connectors" className="text-link underline-offset-2 hover:underline focus-ring rounded-sm">/connectors</Link>, click{" "}
              <span className="font-medium text-foreground">Connect</span> on the Gmail card.
            </>,
            "A small popup will open with Google's sign-in page.",
            "Sign in with the Google account whose contacts you want to pull.",
            <>
              Google will show a consent screen listing{" "}
              <span className="font-mono text-foreground">contacts.readonly</span> (read your
              contacts). Click <span className="font-medium text-foreground">Allow</span>.
            </>,
            "The popup closes and Contact-Ops lands the first pull as proposals in your Review Queue."
          ]}
        />
      </div>

      <Troubleshooting
        items={[
          {
            symptom: '"Popup blocked"',
            fix: (
              <>
                Allow popups for <span className="font-mono text-foreground">{window.location.host}</span>{" "}
                and retry; or use full-page redirect — the Connect button falls back to it
                automatically after the first block.
              </>
            )
          },
          {
            symptom: '"Google warns this app isn\'t verified"',
            fix: (
              <>
                Expected for unverified apps. Click{" "}
                <span className="font-medium text-foreground">Advanced</span> →{" "}
                <span className="font-medium text-foreground">Go to Contact-Ops (unsafe)</span>{" "}
                to proceed. We'll verify the app with Google before public launch.
              </>
            )
          },
          {
            symptom: '"Empty contacts after pulling"',
            fix: (
              <>
                Google Contacts is separate from Gmail interactions. Users who only have
                Gmail-derived contacts (auto-saved from sent mail) may see fewer entries. Try
                exporting from{" "}
                <a
                  href="https://contacts.google.com"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
                >
                  contacts.google.com
                  <ExternalLink
                    className="ml-0.5 inline-block h-3 w-3"
                    strokeWidth={2}
                    aria-hidden="true"
                  />
                </a>{" "}
                and using <Link to="/import" className="text-link underline-offset-2 hover:underline focus-ring rounded-sm">/import</Link> instead.
              </>
            )
          }
        ]}
      />
    </section>
  );
}

// ---- Table of contents (sticky sidebar on lg+) -----------------------------

function TableOfContents() {
  return (
    <nav
      aria-label="On this page"
      className="hidden lg:sticky lg:top-20 lg:block lg:h-fit lg:w-56 lg:shrink-0"
    >
      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <BookOpen className="h-3 w-3" strokeWidth={1.8} aria-hidden="true" />
        On this page
      </p>
      <ul className="space-y-1 text-sm">
        {PROVIDER_SECTIONS.map((def) => (
          <li key={def.id}>
            <a
              href={`#${def.id}`}
              className="focus-ring block rounded-md border-l-2 border-transparent px-2 py-1 text-muted-foreground hover:border-primary hover:bg-primary/5 hover:text-foreground"
            >
              {def.name}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

// ---- Page ------------------------------------------------------------------

export function HelpConnectorsRoute() {
  // When the user lands with a `#provider` hash (e.g. from a card "Learn more"),
  // jump them to that section after first paint so React has rendered the IDs.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hash = window.location.hash.replace("#", "");
    if (!hash) return;
    const node = document.getElementById(hash);
    if (node) {
      // Use a microtask so the layout has settled before scrolling.
      window.requestAnimationFrame(() => {
        node.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "auto"
            : "smooth",
          block: "start"
        });
      });
    }
  }, []);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="space-y-3">
        <div className="flex items-center gap-2.5">
          <span
            className="flex h-9 w-9 items-center justify-center rounded-xl bg-[oklch(var(--co-fuchsia-500)/0.18)] text-[oklch(var(--co-fuchsia-500))]"
            aria-hidden="true"
          >
            <HelpCircle className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <h1 className="text-2xl font-semibold tracking-tight">Connecting a contact source</h1>
        </div>
        <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
          Contact-Ops pulls contacts from three places: iCloud, Microsoft 365, and Gmail. Each
          uses a different auth model — Apple needs an app-specific password; Microsoft and
          Google use OAuth via a sign-in popup. Pick your provider below for the step-by-step.
        </p>
        <div className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-emerald-500)/0.35)] bg-[oklch(var(--co-emerald-500)/0.08)] px-3 py-2.5 text-sm leading-snug text-foreground">
          <Sparkles
            className="mt-0.5 h-4 w-4 shrink-0 text-[oklch(var(--co-emerald-500))]"
            strokeWidth={2}
            aria-hidden="true"
          />
          <span>
            Every pull lands records as <strong className="font-semibold">proposals</strong> in
            your{" "}
            <Link
              to="/review"
              className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
            >
              Review Queue
            </Link>
            . Nothing is applied until you approve.
          </span>
        </div>
      </header>

      <div className="flex gap-8">
        <div className="min-w-0 flex-1 space-y-6">
          {PROVIDER_SECTIONS.map((def) => {
            if (def.id === "icloud") return <IcloudSection key={def.id} def={def} />;
            if (def.id === "m365") return <M365Section key={def.id} def={def} />;
            return <GmailSection key={def.id} def={def} />;
          })}

          <footer className="rounded-xl border border-border bg-card/60 p-4 text-sm leading-relaxed text-muted-foreground">
            <p>
              Need to bulk-import from a file instead?{" "}
              <Link
                to="/import"
                className="text-link underline-offset-2 hover:underline focus-ring rounded-sm"
              >
                Use /import
              </Link>{" "}
              to drop a .vcf exported from any contacts app. Same Review Queue, no live
              connection required.
            </p>
          </footer>
        </div>

        <TableOfContents />
      </div>
    </div>
  );
}

export default HelpConnectorsRoute;
