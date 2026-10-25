import { MCPError } from "@/lib/mcp";

const SCOPE_ERROR_CODES = new Set(["INSUFFICIENT_SCOPE", "FORBIDDEN_SCOPE"]);
const ROLE_ERROR_CODES = new Set(["INSUFFICIENT_ROLE", "FORBIDDEN_ROLE"]);

function requiredScope(message: string): string | undefined {
  const match = message.match(
    /(?:missing required scope(?:\(s\))?|requires? scope(?:\(s\))?)\s*:\s*([^,\s]+)/i
  );
  return match?.[1];
}

/**
 * Keep mutation failures visible at the control that initiated them. Known
 * authorization failures get actionable copy; other errors retain the server
 * message so a real failure is never reduced to an unexplained generic toast.
 */
export function mutationErrorMessage(error: unknown, fallback = "Could not complete this action."): string {
  if (error instanceof MCPError) {
    const code = String(error.code).toUpperCase();
    if (SCOPE_ERROR_CODES.has(code)) {
      const scope = requiredScope(error.message);
      return `Your sign-in is missing the permission${scope ? ` ${scope}` : " required for this action"}. Sign out and back in; if it persists your workspace admin must grant it.`;
    }
    if (ROLE_ERROR_CODES.has(code)) {
      return `Your sign-in is missing the workspace role required for this action (${error.message}). Sign out and back in; if it persists your workspace admin must grant it.`;
    }
    return error.message || fallback;
  }
  if (error instanceof Error) return error.message || fallback;
  if (typeof error === "string" && error.trim()) return error;
  return fallback;
}
