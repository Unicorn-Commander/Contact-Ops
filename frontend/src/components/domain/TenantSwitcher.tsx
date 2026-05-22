import { CircleUser, Building } from "lucide-react";
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
  const currentTenant = tenants.find(t => t.tenant_id === tenant) || tenants[0];

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
      <Button variant="outline" size="icon" className="h-9 w-9">
        <CircleUser className="h-4 w-4 animate-pulse" />
      </Button>
    );
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <Button 
        variant="outline" 
        className="h-9 px-3"
        onClick={() => setOpen(!open)}
        aria-label="Switch tenant"
      >
        <Building className="h-4 w-4 mr-2" />
        <span className="hidden md:inline">{currentTenant?.display_name ?? "Tenant"}</span>
      </Button>
      
      {tenants.length > 1 && open && (
        <div className="absolute right-0 mt-2 w-56 bg-card border border-muted rounded-md shadow-lg z-20">
          <div className="p-1">
            {tenants.map((t) => (
              <button
                key={t.tenant_id}
                className={cn(
                  "flex items-center w-full px-3 py-2 text-sm rounded-md transition-colors",
                  t.tenant_id === tenant ? "bg-violet-500/10 text-violet-500" : "hover:bg-muted"
                )}
                onClick={() => {
                  setTenant(t.tenant_id);
                  const url = new URL(window.location.href);
                  url.searchParams.set("tenant", t.tenant_id);
                  window.history.replaceState({}, "", url);
                  setOpen(false);
                }}
              >
                <Building className="mr-2 h-4 w-4" />
                <span className="flex-1 text-left">{t.display_name}</span>
                {t.tenant_id === tenant && (
                  <span className="h-2 w-2 rounded-full bg-violet-500 ml-2" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
