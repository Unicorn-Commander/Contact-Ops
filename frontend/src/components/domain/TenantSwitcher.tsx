import { Building2, Check, ChevronDown, CircleUser, Loader2, Lock } from "lucide-react";
import { useEffect, useMemo, useState, useRef } from "react";
import { toast } from "sonner";
import { useActiveTenantId, useSwitchWorkspace, useTenants } from "@/hooks/useMcp";
import type { Tenant } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * TenantSwitcher — the active-workspace chip in the app shell.
 *
 * This performs a REAL workspace switch (Phase 4.3): selecting a workspace calls
 * `POST /api/auth/switch-workspace`, which sets the user's tenant attribute in
 * Keycloak; the SPA then silently re-authenticates to pick up the new
 * `tenant_id` claim and `queryClient.clear()`s so no cross-workspace data paints.
 *
 * The active workspace is derived from the TOKEN (`useActiveTenantId`), never
 * from a `?tenant=` URL param. A user with a single membership renders as a
 * non-interactive chip (honest label for "the workspace you're in").
 *
 * Strict / HIPAA targets trigger a step-up redirect (per-workspace MFA) handled
 * inside `useSwitchWorkspace`; the chip shows a lock badge on those rows. NOTE:
 * the step-up redirect does not yet auto-resume the pending switch on return
 * (frontend review S3-3) — the user re-lands authenticated and re-clicks the
 * locked workspace. Tracked as a follow-up.
 */
export function TenantSwitcher() {
  const { data, isLoading } = useTenants();
  const activeTenantId = useActiveTenantId();
  const switchWorkspace = useSwitchWorkspace();

  const [open, setOpen] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const tenants = useMemo<Tenant[]>(() => data?.items ?? [], [data?.items]);
  // Current workspace comes from the token claim; fall back to the first listed
  // workspace only until the claim resolves.
  const currentTenant =
    tenants.find((t) => t.tenant_id === activeTenantId) ?? tenants[0];

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSelect = (t: Tenant) => {
    if (t.tenant_id === activeTenantId || switchWorkspace.isPending) {
      setOpen(false);
      return;
    }
    setPendingId(t.tenant_id);
    switchWorkspace.mutate(t.tenant_id, {
      onSuccess: (outcome) => {
        setPendingId(null);
        setOpen(false);
        switch (outcome.status) {
          case "switched":
            toast.success(`Switched to ${t.display_name}`);
            break;
          case "step_up":
            // A full-page redirect is already in flight; nothing more to do.
            break;
          case "forbidden":
            toast.error("You don't have access to that workspace.");
            break;
          case "unavailable":
            toast.error("Workspace switch is temporarily unavailable. Try again shortly.");
            break;
        }
      },
      onError: (err) => {
        setPendingId(null);
        toast.error(`Couldn't switch workspace: ${err.message}`);
      }
    });
  };

  if (isLoading) {
    return (
      <span
        className="co-v2-glass-calm flex h-9 w-9 items-center justify-center rounded-full text-muted-foreground"
        aria-label="Loading workspace"
      >
        <CircleUser className="h-4 w-4 animate-pulse" strokeWidth={1.8} />
      </span>
    );
  }

  const multi = tenants.length > 1;

  const chipClass =
    "co-v2-glass-calm focus-ring flex h-9 items-center gap-2 rounded-full pl-1.5 pr-3 text-sm font-medium text-foreground";
  const sigil = (
    <span className="bg-gradient-brand flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-primary-foreground">
      <Building2 className="h-3.5 w-3.5" strokeWidth={1.8} />
    </span>
  );
  const label = (
    <span className="hidden max-w-[160px] truncate md:inline">{currentTenant?.display_name ?? "Workspace"}</span>
  );

  // Single workspace → non-interactive chip (honest label, no fake switch).
  if (!multi) {
    return (
      <span className={cn(chipClass, "cursor-default")} aria-label={`Workspace: ${currentTenant?.display_name ?? "—"}`}>
        {sigil}
        {label}
      </span>
    );
  }

  const switching = switchWorkspace.isPending;

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        className={cn(chipClass, "co-v2-hover-bloom pr-2.5 transition-colors", switching && "opacity-80")}
        onClick={() => !switching && setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-busy={switching}
        aria-label="Switch workspace"
        disabled={switching}
      >
        {switching ? (
          <span className="flex h-6 w-6 shrink-0 items-center justify-center">
            <Loader2 className="h-4 w-4 animate-spin text-[oklch(var(--co-brand-300))]" strokeWidth={1.8} />
          </span>
        ) : (
          sigil
        )}
        {label}
        <ChevronDown
          className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
          strokeWidth={1.8}
        />
      </button>

      {open ? (
        <div className="co-v2-glass co-v2-glass-edge absolute left-0 z-50 mt-2 w-64 overflow-hidden p-1" role="menu">
          <p className="px-2.5 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Workspaces
          </p>
          <div className="co-scrollbar max-h-80 overflow-y-auto">
            {tenants.map((t) => {
              const active = t.tenant_id === activeTenantId;
              const locked = t.isolation_mode === "strict" || t.hipaa_mode === true;
              const rowPending = pendingId === t.tenant_id;
              return (
                <button
                  key={t.tenant_id}
                  role="menuitemradio"
                  aria-checked={active}
                  disabled={switching}
                  className={cn(
                    "focus-ring flex w-full items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                    active
                      ? "bg-[oklch(var(--co-brand-500)/0.12)] text-foreground"
                      : "text-muted-foreground hover:bg-[oklch(var(--co-brand-500)/0.08)] hover:text-foreground"
                  )}
                  onClick={() => handleSelect(t)}
                >
                  <span
                    className={cn(
                      "flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
                      active
                        ? "bg-gradient-brand text-primary-foreground"
                        : "bg-[oklch(var(--co-brand-500)/0.12)] text-[oklch(var(--co-brand-300))]"
                    )}
                  >
                    <Building2 className="h-3.5 w-3.5" strokeWidth={1.8} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      <span className="block truncate font-medium">{t.display_name}</span>
                      {locked ? (
                        <Lock
                          className="h-3 w-3 shrink-0 text-muted-foreground"
                          strokeWidth={2}
                          aria-label="Requires step-up verification"
                        />
                      ) : null}
                    </span>
                    {t.role ? (
                      <span className="block truncate text-xs capitalize text-muted-foreground">{t.role}</span>
                    ) : null}
                  </span>
                  {rowPending ? (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[oklch(var(--co-brand-300))]" strokeWidth={2} />
                  ) : active ? (
                    <Check className="h-4 w-4 shrink-0 text-[oklch(var(--co-brand-300))]" strokeWidth={2} />
                  ) : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
