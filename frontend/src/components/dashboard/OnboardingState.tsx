/**
 * Guided onboarding / empty state — shown when the tenant has no contacts.
 *
 * Makes the empty dashboard a feature, not a void: a welcoming header, three
 * import-source cards (Apple / CardDAV, Microsoft 365, Gmail) that route to
 * /settings where the actual import lives, and a couple of secondary "start
 * from scratch" affordances. The import backend is separate — this is the
 * entry UI only.
 */
import type { LucideIcon } from "lucide-react";
import { ArrowRight, Building2, Cloud, Contact, Mail, Sparkles, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type ImportSourceId = "apple" | "microsoft" | "gmail";

type SourceDef = {
  id: ImportSourceId;
  icon: LucideIcon;
  name: string;
  detail: string;
  tone: string;
};

const SOURCES: SourceDef[] = [
  {
    id: "apple",
    icon: Contact,
    name: "Apple / CardDAV",
    detail: "iCloud Contacts or any CardDAV server",
    tone: "text-[oklch(var(--co-sky-500))]"
  },
  {
    id: "microsoft",
    icon: Cloud,
    name: "Microsoft 365",
    detail: "Outlook & Exchange address book",
    tone: "text-[oklch(var(--co-emerald-500))]"
  },
  {
    id: "gmail",
    icon: Mail,
    name: "Gmail",
    detail: "Google Contacts via your account",
    tone: "text-[oklch(var(--co-amber-500))]"
  }
];

export interface OnboardingStateProps {
  /** Called with the chosen source — route to /settings#import in the live app. */
  onImport?: (source: ImportSourceId) => void;
  /** "Add your first person" affordance. */
  onAddPerson?: () => void;
  /** "Create an organization" affordance. */
  onAddOrg?: () => void;
  className?: string;
}

function SourceCard({ source, onImport }: { source: SourceDef; onImport?: (id: ImportSourceId) => void }) {
  const { icon: Icon, name, detail, tone } = source;
  return (
    <button
      type="button"
      onClick={() => onImport?.(source.id)}
      className="focus-ring group/src flex h-full flex-col items-start gap-3 rounded-xl border bg-card p-5 text-left shadow-[var(--shadow-1)] transition-all duration-150 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-[var(--shadow-2)]"
    >
      <span className={cn("flex h-10 w-10 items-center justify-center rounded-lg bg-muted", tone)}>
        <Icon className="h-5 w-5" strokeWidth={1.7} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{name}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{detail}</span>
      </span>
      <span className="mt-auto inline-flex items-center gap-1 pt-1 text-xs font-medium text-link">
        Connect
        <ArrowRight
          className="h-3.5 w-3.5 transition-transform duration-150 group-hover/src:translate-x-0.5"
          strokeWidth={2}
        />
      </span>
    </button>
  );
}

export function OnboardingState({ onImport, onAddPerson, onAddOrg, className }: OnboardingStateProps) {
  return (
    <div className={cn("co-animate-fade-in space-y-6", className)}>
      <Card className="relative overflow-hidden">
        <span
          aria-hidden
          className="pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full bg-primary/10 blur-3xl"
        />
        <CardContent className="relative p-6 sm:p-8">
          <span className="bg-gradient-brand co-glow-brand flex h-12 w-12 items-center justify-center rounded-2xl text-primary-foreground">
            <Sparkles className="h-6 w-6" strokeWidth={1.7} />
          </span>
          <h2 className="mt-4 text-xl font-semibold tracking-tight sm:text-2xl">Bring your contacts in</h2>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
            Contact-Ops becomes your canonical identity hub once your people and organizations land here. Connect a
            source below and the agents start deduping, enriching, and keeping records clean — automatically.
          </p>

          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {SOURCES.map((source) => (
              <SourceCard key={source.id} source={source} onImport={onImport} />
            ))}
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-2 border-t pt-5">
            <p className="mr-1 text-xs text-muted-foreground">Prefer to start small?</p>
            <Button variant="outline" size="sm" className="gap-2" onClick={onAddPerson}>
              <UserPlus className="h-4 w-4" strokeWidth={1.8} />
              Add your first person
            </Button>
            <Button variant="ghost" size="sm" className="gap-2 text-muted-foreground" onClick={onAddOrg}>
              <Building2 className="h-4 w-4" strokeWidth={1.8} />
              Create an organization
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
