/**
 * Connectors — third-party data-source configuration + pull control.
 *
 * Sidebar page between Import and the admin divider. Renders three provider
 * cards (iCloud / Microsoft 365 / Gmail) plus a Recent Runs table beneath.
 *
 * Auth & contract:
 *   - All MCP calls go through `useConnectors` hooks; 401 redirects to OIDC.
 *   - iCloud uses an app-specific password (configure_icloud_connector).
 *   - M365 & Gmail use OAuth — `get_oauth_start_url` returns a redirect URL +
 *     state nonce. Backend's REST callback handles the code exchange and
 *     302s back to `/connectors?success={provider}`. We pop the URL in a new
 *     window (preferred) and fall back to a full-page redirect if blocked.
 *
 * Polling:
 *   - When pull_connector_now returns a `running` row, the page starts polling
 *     list_connector_runs every 3s. Polling stops the moment all rows are
 *     terminal (succeeded / failed / partial).
 *
 * Visuals:
 *   - Honors prefers-reduced-motion via the existing tokens.css guard.
 *   - All state colours are tied to OKLCH custom-property tints, AA in both
 *     modes (sky/emerald/amber/rose), and the brand purple is reserved for
 *     the gradient CTA + page header sigil.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import {
  AlertCircle,
  Cable,
  Cloud,
  CheckCircle2,
  ChevronDown,
  Mail,
  Plug,
  RefreshCw,
  Trash2,
  X
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/design-system/Spinner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger
} from "@/components/ui/dropdown-menu";
import {
  PROVIDER_ACTION_HINT,
  ProviderCard,
  type ProviderCardStatus
} from "@/components/connectors/ProviderCard";
import { IcloudConnectModal } from "@/components/connectors/IcloudConnectModal";
import { RunsTable } from "@/components/connectors/RunsTable";
import { decodeConnectorError } from "@/components/connectors/decodeConnectorError";
import {
  hasInflightRun,
  useConfigureIcloud,
  useConnectorRuns,
  useConnectors,
  useDisconnectConnector,
  useGetOauthStartUrl,
  usePullConnectorNow
} from "@/hooks/useConnectors";
import { UnauthorizedError } from "@/lib/mcp";
import type { Connector, ConnectorProvider, ConnectorRun } from "@/lib/types";
import { cn } from "@/lib/utils";

// ---- Provider definitions --------------------------------------------------

interface ProviderDef {
  provider: ConnectorProvider;
  name: string;
  description: string;
  icon: typeof Cloud;
  iconBg:
    | "bg-[oklch(var(--co-sky-500)/0.18)] text-[oklch(var(--co-sky-500))]"
    | "bg-[oklch(var(--co-amber-500)/0.18)] text-[oklch(var(--co-amber-500))]"
    | "bg-[oklch(var(--co-rose-500)/0.18)] text-[oklch(var(--co-rose-500))]";
}

const PROVIDERS: ProviderDef[] = [
  {
    provider: "icloud",
    name: "iCloud",
    description: "Pull contacts via CardDAV using an app-specific password.",
    icon: Cloud,
    iconBg: "bg-[oklch(var(--co-sky-500)/0.18)] text-[oklch(var(--co-sky-500))]"
  },
  {
    provider: "m365",
    name: "Microsoft 365",
    description: "Pull contacts via Microsoft Graph; sign in with Microsoft.",
    icon: Mail,
    iconBg: "bg-[oklch(var(--co-amber-500)/0.18)] text-[oklch(var(--co-amber-500))]"
  },
  {
    provider: "gmail",
    name: "Gmail",
    description: "Pull contacts via Google People; sign in with Google.",
    icon: Mail,
    iconBg: "bg-[oklch(var(--co-rose-500)/0.18)] text-[oklch(var(--co-rose-500))]"
  }
];

// ---- Helpers ---------------------------------------------------------------

function deriveCardStatus(connector?: Connector): ProviderCardStatus {
  if (!connector) return "not_configured";
  return connector.status;
}

function findConnector(
  connectors: Connector[] | undefined,
  provider: ConnectorProvider
): Connector | undefined {
  return connectors?.find((row) => row.provider === provider);
}

/**
 * Attempt to open the OAuth URL in a popup. If the browser blocks it we fall
 * back to a full-page navigation — the same `?success=<provider>` callback will
 * still fire when the user returns.
 */
function openOauthWindow(url: string): boolean {
  const width = 540;
  const height = 720;
  const left = Math.max(0, (window.screen.availWidth - width) / 2);
  const top = Math.max(0, (window.screen.availHeight - height) / 2);
  const popup = window.open(
    url,
    "contact-ops-connector-oauth",
    `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=yes,status=no,resizable=yes,scrollbars=yes`
  );
  if (popup && !popup.closed) {
    popup.focus();
    return true;
  }
  // Popup blocked — fall back to full navigation.
  window.location.assign(url);
  return false;
}

function providerDisplayName(provider: ConnectorProvider): string {
  return PROVIDERS.find((def) => def.provider === provider)?.name ?? provider;
}

// ---- Confirm-disconnect dialog (inline; we don't scaffold a shared one) ----

interface DisconnectDialogProps {
  open: boolean;
  providerName: string;
  isSubmitting?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

function DisconnectDialog({ open, providerName, isSubmitting, onCancel, onConfirm }: DisconnectDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onCancel())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <div className="flex items-center gap-2.5">
            <span
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-[oklch(var(--co-rose-500)/0.15)] text-[oklch(var(--co-rose-500))]"
              aria-hidden="true"
            >
              <Trash2 className="h-5 w-5" strokeWidth={1.8} />
            </span>
            <div>
              <DialogTitle className="text-base">Disconnect {providerName}?</DialogTitle>
              <DialogDescription className="mt-1 text-xs">
                Already-applied contacts stay in place; you can reconnect later.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button type="button" variant="destructive" onClick={onConfirm} disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label="Disconnecting…" size="sm" />
            ) : (
              <>
                <Trash2 className="h-4 w-4" strokeWidth={1.8} />
                Disconnect
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---- OAuth success banner --------------------------------------------------

function OauthSuccessBanner({
  provider,
  onDismiss
}: {
  provider: ConnectorProvider;
  onDismiss: () => void;
}) {
  const name = providerDisplayName(provider);
  return (
    <div
      role="status"
      className={cn(
        "co-animate-slide-up flex items-start gap-3 rounded-xl border border-[oklch(var(--co-emerald-500)/0.4)] bg-[oklch(var(--co-emerald-500)/0.08)] px-4 py-3 text-sm"
      )}
    >
      <span
        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[oklch(var(--co-emerald-500)/0.18)] text-[oklch(var(--co-emerald-500))]"
        aria-hidden="true"
      >
        <CheckCircle2 className="h-4 w-4" strokeWidth={2} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium leading-snug">{name} connected</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Pulling the first batch now — proposals will appear in your Review Queue shortly.
        </p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" strokeWidth={1.8} />
      </button>
    </div>
  );
}

// ---- OAuth error banner ----------------------------------------------------

function OauthErrorBanner({
  message,
  onDismiss
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "co-animate-slide-up flex items-start gap-3 rounded-xl border border-[oklch(var(--co-rose-500)/0.4)] bg-[oklch(var(--co-rose-500)/0.08)] px-4 py-3 text-sm"
      )}
    >
      <span
        className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[oklch(var(--co-rose-500)/0.18)] text-[oklch(var(--co-rose-500))]"
        aria-hidden="true"
      >
        <AlertCircle className="h-4 w-4" strokeWidth={2} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium leading-snug">Sign-in didn't finish</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{message}</p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" strokeWidth={1.8} />
      </button>
    </div>
  );
}

// ---- Page ------------------------------------------------------------------

export function ConnectorsRoute() {
  const navigate = useNavigate();
  const search = useRouterState({ select: (state) => state.location.search });

  // ---- Read `?success=<provider>` --------------------------------------
  const successProvider = useMemo<ConnectorProvider | null>(() => {
    const raw = (search as Record<string, unknown> | undefined)?.success;
    if (raw === "icloud" || raw === "m365" || raw === "gmail") return raw;
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const value = params.get("success");
      if (value === "icloud" || value === "m365" || value === "gmail") return value;
    }
    return null;
  }, [search]);

  // ---- Read `?error=<CODE>&provider=<provider>` ------------------------
  // The OAuth callback bounces back here when Microsoft/Google refused the
  // exchange (state mismatch, user un-ticked Contacts, etc). We decode the
  // code into actionable copy via decodeConnectorError so the user sees a
  // sentence rather than a raw machine string.
  const oauthErrorInfo = useMemo<{ code: string; provider?: ConnectorProvider } | null>(() => {
    // Prefer the router-provided search object (so the memo re-runs when
    // TanStack Router updates it), then fall back to the raw window URL.
    const fromRouter = (search as Record<string, unknown> | undefined) ?? {};
    const routerCode = typeof fromRouter.error === "string" ? fromRouter.error : null;
    const routerProvider =
      typeof fromRouter.provider === "string" ? fromRouter.provider : null;
    let code = routerCode;
    let providerRaw = routerProvider;
    if (!code && typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      code = params.get("error");
      providerRaw = params.get("provider");
    }
    if (!code) return null;
    const provider: ConnectorProvider | undefined =
      providerRaw === "icloud" || providerRaw === "m365" || providerRaw === "gmail"
        ? providerRaw
        : undefined;
    return { code, provider };
  }, [search]);

  const [bannerProvider, setBannerProvider] = useState<ConnectorProvider | null>(null);
  const [oauthErrorMessage, setOauthErrorMessage] = useState<string | null>(null);

  // ---- Modals + transient state ---------------------------------------
  const [icloudOpen, setIcloudOpen] = useState(false);
  const [icloudError, setIcloudError] = useState<string | null>(null);
  const [disconnectTarget, setDisconnectTarget] = useState<Connector | null>(null);

  // Track which connector_id has an in-flight Pull mutation so we can disable
  // the button + show the inline pulse without waiting for the runs poll.
  const [pullingId, setPullingId] = useState<string | null>(null);

  // ---- Queries + mutations --------------------------------------------
  const connectorsQuery = useConnectors();
  const connectors = connectorsQuery.data?.items ?? [];

  // First-pass fetch: NOT polling. Once a Pull dispatches a `running` run we
  // flip `pollEnabled` true so the runs table refreshes every 3s. We compute
  // the next state from the data itself so the poll stops automatically.
  const initialRunsQuery = useConnectorRuns({ limit: 10 }, false);
  const hasRunning = hasInflightRun(initialRunsQuery.data);
  const [forcePoll, setForcePoll] = useState(false);
  const pollEnabled = forcePoll || hasRunning;
  const runsQuery = useConnectorRuns({ limit: 10 }, pollEnabled);

  // Once there are no more in-flight rows AND we're not actively pulling, drop
  // the force flag so the next render naturally settles refetchInterval to false.
  useEffect(() => {
    if (!hasRunning && forcePoll && !pullingId) {
      setForcePoll(false);
    }
  }, [hasRunning, forcePoll, pullingId]);

  const configureIcloud = useConfigureIcloud();
  const getOauthStartUrl = useGetOauthStartUrl();
  const pullConnectorNow = usePullConnectorNow();
  const disconnectConnector = useDisconnectConnector();

  // ---- OAuth success banner ↔ URL bookkeeping --------------------------
  useEffect(() => {
    if (!successProvider) return;
    setBannerProvider(successProvider);
    // Clean the query param so a refresh doesn't re-show the banner.
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("success");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
    // Auto-dismiss after ~9s.
    const timer = window.setTimeout(() => setBannerProvider(null), 9000);
    return () => window.clearTimeout(timer);
  }, [successProvider]);

  // ---- OAuth error banner ↔ URL bookkeeping ----------------------------
  useEffect(() => {
    if (!oauthErrorInfo) return;
    const decoded = decodeConnectorError(
      { code: oauthErrorInfo.code },
      { provider: oauthErrorInfo.provider }
    );
    setOauthErrorMessage(decoded ?? oauthErrorInfo.code);
    // Drop the `?error=` + `?provider=` so a refresh doesn't re-surface it.
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("error");
      url.searchParams.delete("provider");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
  }, [oauthErrorInfo]);

  // ---- Handlers --------------------------------------------------------
  const handleConnectIcloud = useCallback(() => {
    setIcloudError(null);
    setIcloudOpen(true);
  }, []);

  const handleSubmitIcloud = useCallback(
    async (values: { apple_id: string; app_password: string; display_name: string }) => {
      setIcloudError(null);
      try {
        await configureIcloud.mutateAsync(values);
        setIcloudOpen(false);
      } catch (error) {
        if (error instanceof UnauthorizedError) return;
        const errLike =
          error instanceof Error
            ? error
            : { message: typeof error === "string" ? error : JSON.stringify(error) };
        // Decode known codes (e.g. INVALID_CREDENTIALS) into actionable copy;
        // unknown errors fall through to error.message verbatim so we never
        // hide a genuine bug behind a generic line.
        const decoded = decodeConnectorError(errLike, { provider: "icloud" });
        setIcloudError(
          decoded ??
            (errLike.message
              ? `Couldn't connect iCloud: ${errLike.message}`
              : "Couldn't connect iCloud. Re-check the Apple ID and app-specific password.")
        );
      }
    },
    [configureIcloud]
  );

  const handleStartOauth = useCallback(
    async (provider: Exclude<ConnectorProvider, "icloud">) => {
      try {
        const { redirect_url } = await getOauthStartUrl.mutateAsync({ provider });
        openOauthWindow(redirect_url);
      } catch (error) {
        if (error instanceof UnauthorizedError) return;
        console.error(`OAuth start failed for ${provider}`, error);
      }
    },
    [getOauthStartUrl]
  );

  const handlePull = useCallback(
    async (connectorId: string, dryRun: boolean) => {
      setPullingId(connectorId);
      setForcePoll(true); // poll while the dispatched run propagates
      try {
        await pullConnectorNow.mutateAsync({ connector_id: connectorId, dry_run: dryRun });
      } finally {
        // Even if the mutation errors, the runs table will tell the truth.
        setPullingId(null);
      }
    },
    [pullConnectorNow]
  );

  const handleConfirmDisconnect = useCallback(async () => {
    if (!disconnectTarget) return;
    try {
      await disconnectConnector.mutateAsync({ connector_id: disconnectTarget.id });
    } finally {
      setDisconnectTarget(null);
    }
  }, [disconnectConnector, disconnectTarget]);

  const handleNavigateToReview = useCallback(() => {
    void navigate({ to: "/review" });
  }, [navigate]);

  // ---- Render ----------------------------------------------------------
  const isInitialLoading =
    connectorsQuery.isLoading || (connectorsQuery.isFetching && !connectorsQuery.data);
  const isInitialRunsLoading =
    runsQuery.isLoading || (runsQuery.isFetching && !runsQuery.data);
  const allRuns: ConnectorRun[] = runsQuery.data?.items ?? [];

  const cardsConfigured = connectors.length > 0;

  return (
    <div className="space-y-6">
      <PageHeader />

      {oauthErrorMessage ? (
        <OauthErrorBanner
          message={oauthErrorMessage}
          onDismiss={() => setOauthErrorMessage(null)}
        />
      ) : null}

      {bannerProvider ? (
        <OauthSuccessBanner
          provider={bannerProvider}
          onDismiss={() => setBannerProvider(null)}
        />
      ) : null}

      {isInitialLoading ? (
        <CardGridSkeleton />
      ) : cardsConfigured ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.map((def) => {
            const connector = findConnector(connectors, def.provider);
            return (
              <ProviderCardSlot
                key={def.provider}
                def={def}
                connector={connector}
                isPulling={
                  connector?.id === pullingId ||
                  allRuns.some(
                    (run) =>
                      run.connector_id === connector?.id && run.status === "running"
                  )
                }
                onConnectIcloud={handleConnectIcloud}
                onConnectOauth={handleStartOauth}
                onPull={handlePull}
                onDisconnect={(c) => setDisconnectTarget(c)}
              />
            );
          })}
        </div>
      ) : (
        <EmptyStateGrid
          onConnectIcloud={handleConnectIcloud}
          onConnectOauth={handleStartOauth}
        />
      )}

      <RunsTable runs={allRuns} isLoading={isInitialRunsLoading} />

      <IcloudConnectModal
        open={icloudOpen}
        onOpenChange={(next) => {
          if (!next) setIcloudError(null);
          setIcloudOpen(next);
        }}
        onSubmit={handleSubmitIcloud}
        isSubmitting={configureIcloud.isPending}
        errorMessage={icloudError}
      />

      <DisconnectDialog
        open={Boolean(disconnectTarget)}
        providerName={disconnectTarget ? providerDisplayName(disconnectTarget.provider) : ""}
        isSubmitting={disconnectConnector.isPending}
        onCancel={() => setDisconnectTarget(null)}
        onConfirm={handleConfirmDisconnect}
      />

      {/* Hidden helper — kept here so the sidebar "Review Queue" jump is one click
          away from a freshly-completed pull without a banner button bloat. */}
      <button type="button" className="sr-only" onClick={handleNavigateToReview}>
        Go to Review Queue
      </button>
    </div>
  );
}

// ---- Page header -----------------------------------------------------------

function PageHeader() {
  return (
    <header className="space-y-2">
      <div className="flex items-center gap-2.5">
        <span
          className="flex h-9 w-9 items-center justify-center rounded-xl bg-[oklch(var(--co-fuchsia-500)/0.18)] text-[oklch(var(--co-fuchsia-500))]"
          aria-hidden="true"
        >
          <Cable className="h-5 w-5" strokeWidth={1.8} />
        </span>
        <h1 className="text-2xl font-semibold tracking-tight">Connectors</h1>
      </div>
      <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
        Pull contacts from iCloud, Microsoft 365, or Gmail. Each pull lands records as proposals in
        your <span className="font-medium text-foreground">Review Queue</span> — nothing is applied
        without your nod.
      </p>
    </header>
  );
}

// ---- Card skeleton (initial load) ------------------------------------------

function CardGridSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 3 }).map((_, idx) => (
        <div
          key={idx}
          className="rounded-xl border border-border bg-card p-5 shadow-[var(--shadow-1)]"
        >
          <div className="flex items-center gap-3">
            <Skeleton className="h-11 w-11 rounded-xl" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-56" />
            </div>
          </div>
          <Skeleton className="mt-4 h-3 w-40" />
          <div className="mt-5 flex justify-end gap-2">
            <Skeleton className="h-9 w-24" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---- Empty state -----------------------------------------------------------

function EmptyStateGrid({
  onConnectIcloud,
  onConnectOauth
}: {
  onConnectIcloud: () => void;
  onConnectOauth: (provider: Exclude<ConnectorProvider, "icloud">) => void;
}) {
  return (
    <div className="space-y-5">
      <div
        className={cn(
          "co-animate-fade-in flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-card/40 px-6 py-12 text-center"
        )}
      >
        <span
          className="bg-gradient-brand co-glow-brand flex h-14 w-14 items-center justify-center rounded-2xl text-primary-foreground"
          aria-hidden="true"
        >
          <Plug className="h-6 w-6" strokeWidth={1.8} />
        </span>
        <div className="space-y-1.5">
          <p className="text-base font-semibold tracking-tight">
            Connect a source to start pulling contacts.
          </p>
          <p className="mx-auto max-w-md text-sm leading-relaxed text-muted-foreground">
            Everything is proposed; nothing is applied without your approval.
          </p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {PROVIDERS.map((def) => (
          <ProviderCard
            key={def.provider}
            name={def.name}
            description={def.description}
            icon={
              <def.icon
                className={cn("h-5 w-5", def.iconBg.split(" ").slice(-1)[0])}
                strokeWidth={1.8}
              />
            }
            status="not_configured"
            accent="neutral"
            provider={def.provider}
            actionHint={PROVIDER_ACTION_HINT[def.provider] ?? null}
            actions={
              <Button
                variant="default"
                onClick={() =>
                  def.provider === "icloud"
                    ? onConnectIcloud()
                    : onConnectOauth(def.provider)
                }
              >
                <Plug className="h-4 w-4" strokeWidth={1.8} />
                Connect
              </Button>
            }
          />
        ))}
      </div>
    </div>
  );
}

// ---- ProviderCardSlot — bridges hooks to ProviderCard for one provider -----

interface ProviderCardSlotProps {
  def: ProviderDef;
  connector?: Connector;
  isPulling: boolean;
  onConnectIcloud: () => void;
  onConnectOauth: (provider: Exclude<ConnectorProvider, "icloud">) => void;
  onPull: (connectorId: string, dryRun: boolean) => Promise<void> | void;
  onDisconnect: (connector: Connector) => void;
}

function ProviderCardSlot({
  def,
  connector,
  isPulling,
  onConnectIcloud,
  onConnectOauth,
  onPull,
  onDisconnect
}: ProviderCardSlotProps) {
  const status = deriveCardStatus(connector);
  const Icon = def.icon;
  const iconColorClass = def.iconBg.split(" ").slice(-1)[0];

  const accent: "brand" | "fuchsia" | "neutral" =
    status === "connected" ? "brand" : status === "configured" ? "fuchsia" : "neutral";

  return (
    <ProviderCard
      name={def.name}
      description={def.description}
      icon={<Icon className={cn("h-5 w-5", iconColorClass)} strokeWidth={1.8} />}
      status={status}
      accent={accent}
      provider={def.provider}
      actionHint={
        status === "not_configured" ? PROVIDER_ACTION_HINT[def.provider] ?? null : null
      }
      lastPullAt={connector?.last_pull_at ?? null}
      lastPullSummary={connector?.last_pull_summary ?? null}
      lastError={
        connector?.last_error
          ? decodeConnectorError(connector.last_error, { provider: def.provider }) ??
            connector.last_error
          : null
      }
      isPulling={isPulling}
      actions={
        <ActionRow
          status={status}
          provider={def.provider}
          isPulling={isPulling}
          connector={connector}
          onConnectIcloud={onConnectIcloud}
          onConnectOauth={onConnectOauth}
          onPull={onPull}
          onDisconnect={onDisconnect}
        />
      }
    />
  );
}

// ---- ActionRow — per-status buttons ---------------------------------------

function ActionRow({
  status,
  provider,
  isPulling,
  connector,
  onConnectIcloud,
  onConnectOauth,
  onPull,
  onDisconnect
}: {
  status: ProviderCardStatus;
  provider: ConnectorProvider;
  isPulling: boolean;
  connector?: Connector;
  onConnectIcloud: () => void;
  onConnectOauth: (provider: Exclude<ConnectorProvider, "icloud">) => void;
  onPull: (connectorId: string, dryRun: boolean) => Promise<void> | void;
  onDisconnect: (connector: Connector) => void;
}) {
  if (status === "not_configured" || status === "disconnected") {
    return (
      <Button
        variant={status === "not_configured" ? "default" : "outline"}
        onClick={() =>
          provider === "icloud"
            ? onConnectIcloud()
            : onConnectOauth(provider as Exclude<ConnectorProvider, "icloud">)
        }
      >
        <Plug className="h-4 w-4" strokeWidth={1.8} />
        {status === "not_configured" ? "Connect" : "Reconnect"}
      </Button>
    );
  }
  if (status === "error" && connector) {
    return (
      <>
        <Button variant="outline" onClick={() => onDisconnect(connector)}>
          <Trash2 className="h-4 w-4" strokeWidth={1.8} />
          Disconnect
        </Button>
        <Button
          variant="default"
          onClick={() =>
            provider === "icloud"
              ? onConnectIcloud()
              : onConnectOauth(provider as Exclude<ConnectorProvider, "icloud">)
          }
        >
          <RefreshCw className="h-4 w-4" strokeWidth={1.8} />
          Reconnect
        </Button>
      </>
    );
  }
  // configured / connected
  if (!connector) return null;
  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => onDisconnect(connector)} title="Disconnect">
        <Trash2 className="h-3.5 w-3.5" strokeWidth={1.8} />
        Disconnect
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="default" disabled={isPulling}>
            <ChevronDown className="h-4 w-4" strokeWidth={1.8} />
            Options
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={(event) => {
              event.preventDefault();
              void onPull(connector.id, true);
            }}
          >
            Run with Preview only
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={(event) => {
              event.preventDefault();
              void onPull(connector.id, false);
            }}
          >
            Run normal pull
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button
        variant="gradient"
        onClick={() => void onPull(connector.id, false)}
        disabled={isPulling}
      >
        {isPulling ? (
          <Spinner label="Pulling…" size="sm" />
        ) : (
          <>
            <RefreshCw className="h-4 w-4" strokeWidth={1.8} />
            Pull now
          </>
        )}
      </Button>
    </>
  );
}

export default ConnectorsRoute;
