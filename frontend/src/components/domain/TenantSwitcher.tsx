import { Building2, Check, ChevronDown, CircleUser } from "lucide-react";
import { useEffect, useMemo, useState, useRef } from "react";
import { Button } from "@/components/ui/button";
import { useTenants } from "@/hooks/useMcp";
import { cn } from "@/lib/utils";

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
      <Button variant="outline" size="icon" className="h-9 w-9" aria-label="Loading tenant">
        <CircleUser className="h-4 w-4 animate-pulse" strokeWidth={1.8} />
      </Button>
    );
  }

  const multi = tenants.length > 1;

  return (
    <div className="relative" ref={dropdownRef}>
      <Button
        variant="outline"
        className="h-9 gap-2 px-2.5"
        onClick={() => multi && setOpen(!open)}
        aria-haspopup={multi ? "menu" : undefined}
        aria-expanded={multi ? open : undefined}
        aria-label="Switch tenant"
        disabled={!multi}
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 text-primary">
          <Building2 className="h-3.5 w-3.5" strokeWidth={1.8} />
        </span>
        <span className="hidden max-w-[160px] truncate text-sm font-medium md:inline">
          {currentTenant?.display_name ?? "Tenant"}
        </span>
        {multi ? (
          <ChevronDown
            className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
            strokeWidth={1.8}
          />
        ) : null}
      </Button>

      {multi && open ? (
        <div
          className="absolute left-0 z-50 mt-2 w-64 overflow-hidden rounded-md border bg-popover shadow-[var(--shadow-2)]"
          role="menu"
        >
          <p className="border-b px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Workspaces
          </p>
          <div className="max-h-80 overflow-y-auto p-1">
            {tenants.map((t) => {
              const active = t.tenant_id === tenant;
              return (
                <button
                  key={t.tenant_id}
                  role="menuitemradio"
                  aria-checked={active}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                    active ? "bg-primary/10 text-foreground" : "hover:bg-muted"
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
                      "flex h-6 w-6 shrink-0 items-center justify-center rounded",
                      active ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"
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
                  {active ? <Check className="h-4 w-4 shrink-0 text-primary" strokeWidth={2} /> : null}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
