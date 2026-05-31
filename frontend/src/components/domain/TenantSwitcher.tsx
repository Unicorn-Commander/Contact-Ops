import { Building2, Check, ChevronDown, CircleUser } from "lucide-react";
import { useEffect, useMemo, useState, useRef } from "react";
import { useTenants } from "@/hooks/useMcp";
import { cn } from "@/lib/utils";

/**
 * TenantSwitcher — the active-workspace chip in the app shell, restyled to the
 * Design Language v2 glass look.
 *
 * HONESTY CONTRACT (do not break): this reflects the REAL active tenant from the
 * live `useTenants()` data. It never fabricates a workspace list. When the user
 * belongs to a single tenant it renders as a NON-INTERACTIVE chip (no menu, no
 * switch affordance) — just a prettier label for "the workspace you're in". The
 * multi-tenant dropdown only lists tenants the API actually returned, and the
 * "switch" it performs is the same `?tenant=` URL re-scope the component already
 * did — no new behavior was invented here. (True multi-workspace switching that
 * re-scopes all queries is a later backend phase; out of scope for this restyle.)
 */
export function TenantSwitcher() {
  const { data, isLoading } = useTenants();
  const [tenant, setTenant] = useState<string>(() => new URLSearchParams(window.location.search).get("tenant") ?? "");
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const tenants = useMemo(() => data?.items ?? [], [data?.items]);
  const currentTenant = tenants.find((t) => t.tenant_id === tenant) || tenants[0];

  useEffect(() => {
    if (!tenant && tenants[0]) setTenant(tenants[0].tenant_id);
  }, [tenant, tenants]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

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

  // Shared glass chip surface for both the interactive trigger and the static
  // single-tenant chip — same v2 treatment so the bar reads consistently.
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

  // Single workspace → a prettier, NON-INTERACTIVE chip. No menu, no fake switch.
  if (!multi) {
    return (
      <span className={cn(chipClass, "cursor-default")} aria-label={`Workspace: ${currentTenant?.display_name ?? "—"}`}>
        {sigil}
        {label}
      </span>
    );
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        className={cn(chipClass, "co-v2-hover-bloom pr-2.5 transition-colors")}
        onClick={() => setOpen(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Switch workspace"
      >
        {sigil}
        {label}
        <ChevronDown
          className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
          strokeWidth={1.8}
        />
      </button>

      {open ? (
        <div
          className="co-v2-glass co-v2-glass-edge absolute left-0 z-50 mt-2 w-64 overflow-hidden p-1"
          role="menu"
        >
          <p className="px-2.5 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Workspaces
          </p>
          <div className="co-scrollbar max-h-80 overflow-y-auto">
            {tenants.map((t) => {
              const active = t.tenant_id === tenant;
              return (
                <button
                  key={t.tenant_id}
                  role="menuitemradio"
                  aria-checked={active}
                  className={cn(
                    "focus-ring flex w-full items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-2 text-left text-sm transition-colors",
                    active
                      ? "bg-[oklch(var(--co-brand-500)/0.12)] text-foreground"
                      : "text-muted-foreground hover:bg-[oklch(var(--co-brand-500)/0.08)] hover:text-foreground"
                  )}
                  onClick={() => {
                    setTenant(t.tenant_id);
                    const url = new URL(window.location.href);
                    url.searchParams.set("tenant", t.tenant_id);
                    window.history.replaceState({}, "", url);
                    setOpen(false);
                  }}
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
                    <span className="block truncate font-medium">{t.display_name}</span>
                    {t.role ? (
                      <span className="block truncate text-xs capitalize text-muted-foreground">{t.role}</span>
                    ) : null}
                  </span>
                  {active ? (
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
