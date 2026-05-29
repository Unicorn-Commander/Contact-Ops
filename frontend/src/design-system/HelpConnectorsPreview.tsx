/**
 * HelpConnectorsPreview — auth-free showcase of /help/connectors.
 *
 * Lives at /design-system/help-connectors so the page is screenshottable
 * without Keycloak or any backend. Renders the same content as
 * `HelpConnectorsRoute` inside a sidebar + topbar frame, switchable across
 * the two themes (dark + light) and at the 375px mobile width.
 *
 * Variants:
 *   - "top"          (default) — full page from the top
 *   - "icloud-focus" — same page scrolled to the iCloud section so the
 *                      2FA callout is visible without the harness needing
 *                      to scroll programmatically
 *   - "mobile"       — 375px-wide mobile frame
 *
 * Everything is presentational — uses the same copy as
 * HelpConnectorsRoute but rendered as a "mock" so we don't need TanStack
 * Router context.
 */
import { useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Bell,
  BookOpen,
  BookUser,
  Cable,
  CheckCircle2,
  Cloud,
  ExternalLink,
  HelpCircle,
  History,
  KeyRound,
  LifeBuoy,
  LogIn,
  Mail,
  Menu,
  Search,
  Sparkles,
  Upload
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ThemeMode } from "@/design-system/tokens";

// ---- Top-bar / sidebar mocks (matches ConnectorsPreview) ------------------

function MockTopBar() {
  return (
    <header className="co-glass sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border px-3">
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground lg:hidden">
        <Menu className="h-4 w-4" strokeWidth={1.8} />
      </span>
      <div className="flex items-center gap-2 rounded-md border border-border bg-background/60 px-2 py-1 text-[11px] text-muted-foreground">
        <BookUser className="h-3.5 w-3.5 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
        Magic Unicorn
      </div>
      <button className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background/60 px-2 text-[11px] text-muted-foreground">
        <Search className="h-3.5 w-3.5" strokeWidth={1.8} />
        Search…
      </button>
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground">
        <Bell className="h-4 w-4" strokeWidth={1.8} />
      </span>
    </header>
  );
}

function MockSidebar({ compact }: { compact?: boolean }) {
  if (compact) return null;
  const items = [
    { label: "Dashboard", icon: History, accent: "text-[oklch(var(--co-brand-500))]" },
    { label: "People", icon: History, accent: "text-[oklch(var(--co-sky-500))]" },
    { label: "Organizations", icon: History, accent: "text-[oklch(var(--co-emerald-500))]" },
    { label: "Tags", icon: History, accent: "text-[oklch(var(--co-amber-500))]" },
    { label: "Graph", icon: History, accent: "text-[oklch(var(--co-fuchsia-500))]" },
    { label: "Review Queue", icon: History, accent: "text-[oklch(var(--co-rose-500))]" },
    { label: "Import", icon: Upload, accent: "text-[oklch(var(--co-emerald-500))]" },
    {
      label: "Connectors",
      icon: Cable,
      accent: "text-[oklch(var(--co-fuchsia-500))]"
    }
  ] as const;
  return (
    <aside className="hidden w-44 shrink-0 border-r border-border bg-[oklch(var(--sidebar))] lg:block">
      <div className="flex h-14 items-center gap-2 border-b border-border px-3">
        <span className="bg-gradient-brand flex h-7 w-7 items-center justify-center rounded-lg text-primary-foreground">
          <BookUser className="h-3.5 w-3.5" strokeWidth={1.8} />
        </span>
        <span className="text-xs font-semibold">Contact-Ops</span>
      </div>
      <nav className="space-y-1 p-2">
        {items.map((item) => (
          <span
            key={item.label}
            className="flex h-8 items-center gap-2 rounded-md border-l-2 border-transparent px-2 text-[11px] font-medium text-muted-foreground"
          >
            <item.icon className={cn("h-3.5 w-3.5", item.accent)} strokeWidth={1.8} />
            {item.label}
          </span>
        ))}
      </nav>
    </aside>
  );
}

// ---- Section data (mirrors HelpConnectors.tsx) ---------------------------

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

// ---- Mock section primitives ---------------------------------------------

function MockChip({ def }: { def: ProviderSectionDef }) {
  const Icon = def.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
        CHIP_CLASSES[def.chipTone]
      )}
    >
      <Icon className="h-3 w-3" strokeWidth={1.8} />
      {def.name}
    </span>
  );
}

function MockStepList({ items }: { items: React.ReactNode[] }) {
  return (
    <ol className="list-decimal space-y-2 pl-5 text-sm leading-relaxed marker:text-muted-foreground">
      {items.map((item, idx) => (
        <li key={idx}>{item}</li>
      ))}
    </ol>
  );
}

interface MockTrouble {
  symptom: React.ReactNode;
  fix: React.ReactNode;
}

function MockTroubleshooting({ items }: { items: MockTrouble[] }) {
  return (
    <div className="space-y-3">
      <h4 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
        <LifeBuoy className="h-4 w-4 text-[oklch(var(--co-amber-500))]" strokeWidth={1.8} />
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
              />
              <span>{item.fix}</span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function MockSection({ def, children }: { def: ProviderSectionDef; children: React.ReactNode }) {
  const SigilIcon = def.icon;
  const AuthIcon = def.authIcon;
  const authTone =
    def.chipTone === "sky"
      ? "text-[oklch(var(--co-sky-500))]"
      : def.chipTone === "amber"
        ? "text-[oklch(var(--co-amber-500))]"
        : "text-[oklch(var(--co-rose-500))]";
  return (
    <section
      id={def.id}
      className="space-y-5 rounded-xl border border-border bg-card p-6 shadow-[var(--shadow-1)]"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
              SIGIL_CLASSES[def.chipTone]
            )}
          >
            <SigilIcon className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-xl font-semibold tracking-tight">Connecting {def.name}</h2>
              <MockChip def={def} />
            </div>
            <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <AuthIcon className={cn("h-3 w-3", authTone)} strokeWidth={1.8} />
              {def.authShape}
            </p>
          </div>
        </div>
      </header>
      <p className="text-sm leading-relaxed text-muted-foreground">{def.intro}</p>
      {children}
    </section>
  );
}

// ---- Pre-rendered sections (copy verbatim) -------------------------------

function MockIcloud({ def }: { def: ProviderSectionDef }) {
  return (
    <MockSection def={def}>
      <div className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-amber-500)/0.35)] bg-[oklch(var(--co-amber-500)/0.08)] px-3 py-2.5 text-sm leading-snug text-foreground">
        <AlertCircle
          className="mt-0.5 h-4 w-4 shrink-0 text-[oklch(var(--co-amber-500))]"
          strokeWidth={2}
        />
        <span>
          <strong className="font-semibold">Apple requires two-factor authentication</strong>{" "}
          enabled on your Apple ID before you can generate an app-specific password. If 2FA isn't
          enabled, the App-Specific Passwords menu won't appear on your account page.
        </span>
      </div>
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <CheckCircle2 className="h-4 w-4 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
          Steps
        </h3>
        <MockStepList
          items={[
            <>
              Open{" "}
              <span className="text-link underline-offset-2">
                account.apple.com
                <ExternalLink className="ml-0.5 inline-block h-3 w-3" strokeWidth={2} />
              </span>
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
              Label it <span className="font-mono text-foreground">Contact-Ops</span> (or whatever
              you'll recognize later).
            </>,
            <>
              Apple will show a 16-character password formatted as{" "}
              <span className="font-mono text-foreground">xxxx-xxxx-xxxx-xxxx</span>.{" "}
              <span className="font-medium text-foreground">Copy it now</span> — Apple only shows
              this password once.
            </>,
            <>
              Back on <span className="text-link underline-offset-2">/connectors</span>, click{" "}
              <span className="font-medium text-foreground">Connect</span> on the iCloud card and
              paste the password (dashes optional).
            </>
          ]}
        />
      </div>
      <MockTroubleshooting
        items={[
          {
            symptom: '"App-Specific Passwords menu isn\'t showing"',
            fix: (
              <>
                Enable 2FA on your Apple ID first. Apple gates the App-Specific Passwords menu
                behind two-factor enrollment; once 2FA is on, refresh the account page and the menu
                will appear under <span className="font-medium text-foreground">Sign-In and
                Security</span>.
              </>
            )
          },
          {
            symptom: '"Apple says the credentials are invalid"',
            fix: (
              <>
                Confirm the password was copied from{" "}
                <span className="text-link underline-offset-2">appleid.apple.com</span> (not your
                real Apple ID password) and was generated within the last few minutes.
                App-specific passwords expire if unused — re-generate one and paste the new value.
              </>
            )
          }
        ]}
      />
    </MockSection>
  );
}

function MockM365({ def }: { def: ProviderSectionDef }) {
  return (
    <MockSection def={def}>
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <CheckCircle2 className="h-4 w-4 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
          Steps
        </h3>
        <MockStepList
          items={[
            <>
              On <span className="text-link underline-offset-2">/connectors</span>, click{" "}
              <span className="font-medium text-foreground">Connect</span> on the Microsoft 365
              card.
            </>,
            "A small popup will open with Microsoft's sign-in page.",
            "Sign in with the Microsoft account whose contacts you want to pull (work, school, or personal).",
            <>
              Microsoft will show a consent screen listing{" "}
              <span className="font-mono text-foreground">Contacts.Read</span> (read your contacts).
              Click <span className="font-medium text-foreground">Accept</span>.
            </>,
            "The popup closes and Contact-Ops lands the first pull as proposals in your Review Queue."
          ]}
        />
      </div>
      <MockTroubleshooting
        items={[
          {
            symptom: '"Popup blocked"',
            fix: (
              <>
                Allow popups for{" "}
                <span className="font-mono text-foreground">contacts.magicunicorn.dev</span> and
                retry; or use full-page redirect — the Connect button falls back to it
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
                  https://mcp.contacts.magicunicorn.dev/api/connectors/m365/oauth/callback
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
    </MockSection>
  );
}

function MockGmail({ def }: { def: ProviderSectionDef }) {
  return (
    <MockSection def={def}>
      <div className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <CheckCircle2 className="h-4 w-4 text-[oklch(var(--co-emerald-500))]" strokeWidth={1.8} />
          Steps
        </h3>
        <MockStepList
          items={[
            <>
              On <span className="text-link underline-offset-2">/connectors</span>, click{" "}
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
      <MockTroubleshooting
        items={[
          {
            symptom: '"Popup blocked"',
            fix: (
              <>
                Allow popups for{" "}
                <span className="font-mono text-foreground">contacts.magicunicorn.dev</span> and
                retry; or use full-page redirect — the Connect button falls back to it
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
                <span className="font-medium text-foreground">Go to Contact-Ops (unsafe)</span> to
                proceed. We'll verify the app with Google before public launch.
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
                <span className="text-link underline-offset-2">
                  contacts.google.com
                  <ExternalLink className="ml-0.5 inline-block h-3 w-3" strokeWidth={2} />
                </span>{" "}
                and using <span className="text-link underline-offset-2">/import</span> instead.
              </>
            )
          }
        ]}
      />
    </MockSection>
  );
}

function MockToc() {
  return (
    <nav
      aria-label="On this page"
      className="hidden lg:sticky lg:top-20 lg:block lg:h-fit lg:w-56 lg:shrink-0"
    >
      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <BookOpen className="h-3 w-3" strokeWidth={1.8} />
        On this page
      </p>
      <ul className="space-y-1 text-sm">
        {PROVIDER_SECTIONS.map((def) => (
          <li key={def.id}>
            <span className="block rounded-md border-l-2 border-transparent px-2 py-1 text-muted-foreground">
              {def.name}
            </span>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function MockBody({ icloudFirst }: { icloudFirst?: boolean }) {
  // icloudFirst rearranges sections so the iCloud (2FA callout) variant is
  // visible at the top of the frame without scrolling.
  const sections = icloudFirst
    ? [PROVIDER_SECTIONS[0]]
    : PROVIDER_SECTIONS;
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="space-y-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[oklch(var(--co-fuchsia-500)/0.18)] text-[oklch(var(--co-fuchsia-500))]">
            <HelpCircle className="h-5 w-5" strokeWidth={1.8} />
          </span>
          <h1 className="text-2xl font-semibold tracking-tight">Connecting a contact source</h1>
        </div>
        <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
          Contact-Ops pulls contacts from three places: iCloud, Microsoft 365, and Gmail. Each uses
          a different auth model — Apple needs an app-specific password; Microsoft and Google use
          OAuth via a sign-in popup. Pick your provider below for the step-by-step.
        </p>
        <div className="flex items-start gap-2 rounded-md border border-[oklch(var(--co-emerald-500)/0.35)] bg-[oklch(var(--co-emerald-500)/0.08)] px-3 py-2.5 text-sm leading-snug text-foreground">
          <Sparkles
            className="mt-0.5 h-4 w-4 shrink-0 text-[oklch(var(--co-emerald-500))]"
            strokeWidth={2}
          />
          <span>
            Every pull lands records as <strong className="font-semibold">proposals</strong> in your{" "}
            <span className="text-link underline-offset-2">Review Queue</span>. Nothing is applied
            until you approve.
          </span>
        </div>
      </header>
      <div className="flex gap-8">
        <div className="min-w-0 flex-1 space-y-6">
          {sections.map((def) => {
            if (def.id === "icloud") return <MockIcloud key={def.id} def={def} />;
            if (def.id === "m365") return <MockM365 key={def.id} def={def} />;
            return <MockGmail key={def.id} def={def} />;
          })}
          {!icloudFirst ? (
            <footer className="rounded-xl border border-border bg-card/60 p-4 text-sm leading-relaxed text-muted-foreground">
              <p>
                Need to bulk-import from a file instead?{" "}
                <span className="text-link underline-offset-2">Use /import</span> to drop a .vcf
                exported from any contacts app. Same Review Queue, no live connection required.
              </p>
            </footer>
          ) : null}
        </div>
        <MockToc />
      </div>
    </div>
  );
}

// ---- Frame -----------------------------------------------------------------

type Variant = "top" | "icloud-focus" | "mobile";

function Frame({
  theme,
  variant,
  width = "100%",
  compact = false
}: {
  theme: ThemeMode;
  variant: Variant;
  width?: string | number;
  compact?: boolean;
}) {
  return (
    <div
      data-theme={theme}
      style={{ width }}
      className={cn(
        "relative isolate overflow-hidden rounded-[var(--radius-lg)] border border-border bg-background text-foreground shadow-[var(--shadow-2)]"
      )}
    >
      <div className="flex">
        <MockSidebar compact={compact} />
        <div className="min-w-0 flex-1">
          <MockTopBar />
          <div className={cn("p-4 lg:p-6", compact && "p-3")}>
            <MockBody icloudFirst={variant === "icloud-focus"} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- Public preview entry -------------------------------------------------

export function HelpConnectorsPreview() {
  const previews = useMemo(
    () => [
      { theme: "dark" as ThemeMode, variant: "top" as Variant, title: "Top of page (dark)" },
      { theme: "light" as ThemeMode, variant: "top" as Variant, title: "Top of page (light)" },
      {
        theme: "dark" as ThemeMode,
        variant: "icloud-focus" as Variant,
        title: "iCloud section with 2FA callout (dark)"
      },
      {
        theme: "light" as ThemeMode,
        variant: "icloud-focus" as Variant,
        title: "iCloud section with 2FA callout (light)"
      }
    ],
    []
  );

  const [activeIdx, setActiveIdx] = useState(0);
  const active = previews[activeIdx];

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">Help · Connecting a contact source</h2>
        <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
          The /help/connectors deep walkthrough. iCloud uses an app-specific password; Microsoft 365
          and Gmail use OAuth via sign-in popup. Each section has a numbered step list and a
          troubleshooting subsection.
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {previews.map((preview, idx) => (
          <button
            key={`${preview.theme}-${preview.variant}-${idx}`}
            type="button"
            onClick={() => setActiveIdx(idx)}
            className={cn(
              "rounded-md border px-2 py-1 text-[11px] font-medium transition-colors",
              activeIdx === idx
                ? "border-primary bg-primary/10 text-foreground"
                : "border-border bg-card text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {preview.title}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">{active.title}</h3>
        <Frame theme={active.theme} variant={active.variant} />
      </div>

      <div className="grid gap-6 2xl:grid-cols-2">
        {previews.map((preview) => (
          <div key={`grid-${preview.theme}-${preview.variant}`} className="space-y-2">
            <h3 className="text-sm font-medium">{preview.title}</h3>
            <Frame theme={preview.theme} variant={preview.variant} />
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Mobile (375px) · top of page (dark)</h3>
        <div className="mx-auto w-[375px] max-w-full">
          <Frame theme="dark" variant="mobile" width={375} compact />
        </div>
      </div>
    </section>
  );
}

export default HelpConnectorsPreview;
