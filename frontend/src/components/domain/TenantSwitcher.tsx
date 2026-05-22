import { ChevronsUpDown } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { useTenants } from "@/hooks/useMcp";

export function TenantSwitcher() {
  const { data } = useTenants();
  const [tenant, setTenant] = useState<string>(() => new URLSearchParams(window.location.search).get("tenant") ?? "");
  const tenants = useMemo(() => data?.items ?? [], [data?.items]);

  useEffect(() => {
    if (!tenant && tenants[0]) setTenant(tenants[0].tenant_id);
  }, [tenant, tenants]);

  return (
    <label className="flex items-center gap-2 text-sm">
      <select
        value={tenant}
        onChange={(event) => {
          setTenant(event.target.value);
          const url = new URL(window.location.href);
          url.searchParams.set("tenant", event.target.value);
          window.history.replaceState({}, "", url);
        }}
        className="focus-ring h-9 rounded-md border bg-background px-3 text-sm"
      >
        {tenants.length ? tenants.map((item) => <option key={item.tenant_id} value={item.tenant_id}>{item.display_name}</option>) : <option>Current tenant</option>}
      </select>
      <Button variant="ghost" size="icon" aria-label="Tenant menu">
        <ChevronsUpDown className="h-4 w-4" />
      </Button>
    </label>
  );
}
