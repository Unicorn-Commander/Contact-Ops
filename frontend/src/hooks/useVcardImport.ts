/**
 * useVcardImport — multipart contact-import hook (vCard AND CSV).
 *
 * Despite the name (kept so existing imports don't churn), this drives BOTH
 * import endpoints and picks one by the file's extension:
 *   - `.vcf` / `.vcard`  → POST /import/vcard
 *   - `.csv`             → POST /import/csv   (Google Contacts / LinkedIn)
 * Both share the same multipart contract and the same JSON response shape, so
 * the page is agnostic to which one ran.
 *
 * Mirrors the same patterns as `useMcp` / `useChat`:
 *   - Bearer token comes from `react-oidc-context` via `useAccessToken()`.
 *   - 401 triggers `signinRedirect()` (no UI fallback needed; the auth gate
 *     remounts the whole tree once the user comes back).
 *   - 413 is surfaced as a typed sentinel so the page can render a precise
 *     "file too large" message without re-parsing the server detail string.
 *
 * The hook owns the multipart construction (file + dry_run flag) so the page
 * stays presentational. Both endpoints hang off the same MCP base / Traefik
 * router as `/mcp` + `/chat`.
 *
 * Contract — `POST /import/vcard` and `POST /import/csv`:
 *
 *   Request (multipart):
 *     file:    <.vcf | .csv>
 *     dry_run: "true" | "false"
 *
 *   Response 200 JSON:
 *     parsed_count: number
 *     proposed_count: number
 *     deduped_count: number      // collapsed duplicates WITHIN the file
 *     duplicate_count: number    // matched a person already in your contacts
 *     skipped_count: number
 *     errors: Array<{ index: number, reason: string }>
 *     preview: Array<{ display_name, emails, phones, organization?, duplicate? }>
 *
 *   413 → file too large (cap 50 MiB)
 *   400 → `detail` carries the user-facing reason ("not a vCard"/"unrecognized CSV", …)
 */
import { useMutation } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { useAccessToken } from "@/hooks/useMcp";
import { env } from "@/lib/env";
import { UnauthorizedError } from "@/lib/mcp";

// ---- Contract types (match Codex backend field names verbatim) ------------

export interface ImportError {
  index: number;
  reason: string;
}

export interface ImportPreviewRecord {
  display_name: string;
  emails: string[];
  phones: string[];
  organization?: string;
  /** True when this record already matches a person in your contacts. */
  duplicate?: boolean;
}

export interface VcardImportResult {
  parsed_count: number;
  proposed_count: number;
  deduped_count: number;
  /** Records that matched a person already in your contacts (skipped, not re-proposed). */
  duplicate_count?: number;
  skipped_count: number;
  errors: ImportError[];
  preview: ImportPreviewRecord[];
}

// ---- Errors ----------------------------------------------------------------

export class FileTooLargeError extends Error {
  constructor(message = "File too large — must be under 50 MiB.") {
    super(message);
    this.name = "FileTooLargeError";
  }
}

export class ImportValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ImportValidationError";
  }
}

export class ImportTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ImportTransportError";
  }
}

// ---- Constants -------------------------------------------------------------

/** Hard client-side cap. Matches the 50 MiB server limit so we don't even start
 *  the multipart for a file we know the backend will reject. */
export const MAX_VCARD_BYTES = 50 * 1024 * 1024;
/** Same cap; clearer name now that this hook also handles CSV. */
export const MAX_IMPORT_BYTES = MAX_VCARD_BYTES;

/** Choose the import endpoint from the file's extension. The server is the
 *  source of truth (it validates content), so a `.txt`-named vCard still works
 *  by falling through to the vCard endpoint. */
function endpointForFile(file: File): string {
  return file.name.toLowerCase().endsWith(".csv") ? "/import/csv" : "/import/vcard";
}

export interface UseVcardImportArgs {
  file: File;
  dryRun: boolean;
  /** Trust this import → auto-approve high-confidence rows (skip the queue). */
  autoApprove?: boolean;
}

// ---- Hook ------------------------------------------------------------------

/**
 * `useVcardImport` returns a `react-query` mutation. Call `mutate({file, dryRun})`
 * or `mutateAsync(...)` to upload. Errors are typed so the page can branch on
 * `instanceof FileTooLargeError` / `ImportValidationError` etc.
 */
export function useVcardImport() {
  const auth = useAuth();
  const token = useAccessToken();

  return useMutation<VcardImportResult, Error, UseVcardImportArgs>({
    mutationFn: async ({ file, dryRun, autoApprove }) => {
      if (file.size > MAX_IMPORT_BYTES) {
        throw new FileTooLargeError();
      }

      const form = new FormData();
      form.append("file", file);
      form.append("dry_run", dryRun ? "true" : "false");
      form.append("auto_approve", autoApprove ? "true" : "false");

      let response: Response;
      try {
        response = await fetch(`${env.mcpBaseUrl}${endpointForFile(file)}`, {
          method: "POST",
          headers: {
            // Do NOT set Content-Type manually — the browser sets the multipart
            // boundary automatically. Setting it by hand strips the boundary
            // and the server reads an empty body.
            Authorization: `Bearer ${token}`
          },
          body: form
        });
      } catch (caught) {
        // Network failure (CORS preflight, DNS, dropped connection, …) — surface
        // as a transport error so the page can show a Retry button.
        const message = caught instanceof Error ? caught.message : "Network error.";
        throw new ImportTransportError(message);
      }

      if (response.status === 401) {
        // Same pattern as `useMcp` — kick off the OIDC redirect and let the
        // caller bubble the typed error if they care.
        void auth.signinRedirect();
        throw new UnauthorizedError();
      }
      if (response.status === 413) {
        throw new FileTooLargeError();
      }

      if (!response.ok) {
        // Try to read a `detail` field per FastAPI convention; fall back to a
        // generic HTTP-status message.
        let detail = "";
        try {
          const body = (await response.json()) as { detail?: string };
          if (typeof body.detail === "string") detail = body.detail;
        } catch {
          // body wasn't JSON — ignore, we'll fall back to the status text.
        }
        if (response.status >= 400 && response.status < 500) {
          throw new ImportValidationError(
            detail || `Server rejected the file (HTTP ${response.status}).`
          );
        }
        throw new ImportTransportError(
          detail || `Upload failed with HTTP ${response.status}.`
        );
      }

      const parsed = (await response.json()) as VcardImportResult;
      return parsed;
    }
  });
}
