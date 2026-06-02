/**
 * useNotes — query + mutation hooks for the timestamped notes log.
 *
 * Phase 1 surface is the Settings page (target_type='user', no target_id),
 * but the API is generalized so person/org pages can drop the same component
 * in later just by passing a different `target_type` + `target_id`.
 *
 * Pattern adopted (matches `useMcp.ts`):
 *   - Bearer token comes from `react-oidc-context` via `useAccessToken()`.
 *   - Reads use `useAuthedQuery`-style guards (signinRedirect on 401).
 *   - Writes use `useMutation` directly so we can scope `invalidateQueries`
 *     to just the notes list — siblings (people, orgs, tags, etc.) don't need
 *     to refetch when a note is appended.
 *   - MCP tool names + arg shapes are owned by `lib/mcp.ts` so the orchestrator
 *     can audit the contract in one place.
 *
 * Pagination is offset-based to match the simple `list_notes(limit, offset)`
 * shape exposed by Codex's backend track. The component below stitches pages
 * by cumulative offset and lets the user click "Load more"; React Query holds
 * the union as a single in-memory `pages` array.
 */
import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "react-oidc-context";
import { useQueryKeys } from "@/hooks/useMcp";
import { UnauthorizedError, tools } from "@/lib/mcp";
import type { Note, NoteTargetType, UUID } from "@/lib/types";

export interface UseNotesListArgs {
  targetType?: NoteTargetType;
  targetId?: UUID | null;
  pageSize?: number;
}

export interface UseNotesListReturn {
  notes: Note[];
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  refetch: () => Promise<unknown>;
  /** Query key tail — used by mutations to invalidate just this list. */
  queryKey: readonly unknown[];
}

const DEFAULT_PAGE_SIZE = 20;

/**
 * Paginated, newest-first list of notes for a given target.
 *
 * The hook owns the cumulative page state so the consumer just calls
 * `loadMore()` — no need to track offsets manually.
 */
export function useNotesList({
  targetType = "user",
  targetId = null,
  pageSize = DEFAULT_PAGE_SIZE
}: UseNotesListArgs = {}): UseNotesListReturn {
  const auth = useAuth();
  const token = auth.user?.access_token;
  const queryClient = useQueryClient();
  const qk = useQueryKeys();

  const [visibleCount, setVisibleCount] = useState(pageSize);
  const queryKey = useMemo(
    () => [...qk.notes(targetType, targetId, pageSize), { visibleCount }] as const,
    [qk, targetType, targetId, pageSize, visibleCount]
  );

  const query = useQuery({
    queryKey,
    enabled: Boolean(token),
    queryFn: async () => {
      try {
        const result = await tools.listNotes(
          {
            target_type: targetType,
            target_id: targetId ?? undefined,
            limit: visibleCount,
            offset: 0
          },
          token ?? ""
        );
        return result;
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    }
  });

  const items = query.data?.items ?? [];
  const totalCount = query.data?.total_count ?? query.data?.count ?? items.length;
  const hasMore = items.length < totalCount;

  const loadMore = useCallback(() => {
    setVisibleCount((prev) => prev + pageSize);
  }, [pageSize]);

  const refetch = useCallback(async () => {
    setVisibleCount(pageSize);
    return queryClient.invalidateQueries({
      queryKey: qk.notes(targetType, targetId, pageSize)
    });
  }, [qk, pageSize, queryClient, targetType, targetId]);

  return {
    notes: items,
    isLoading: query.isPending,
    isError: query.isError,
    error: (query.error as Error | null) ?? null,
    hasMore,
    loadingMore: query.isFetching && visibleCount > pageSize,
    loadMore,
    refetch,
    queryKey: qk.notes(targetType, targetId, pageSize)
  };
}

export interface UseAppendNoteArgs {
  targetType?: NoteTargetType;
  targetId?: UUID | null;
}

export interface AppendNotePayload {
  text: string;
  source?: Record<string, unknown> | null;
}

/**
 * Append a note to the given scope. Invalidates only the matching list query
 * so unrelated screens (people/orgs/tags) don't refetch.
 */
export function useAppendNote({ targetType = "user", targetId = null }: UseAppendNoteArgs = {}) {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();

  return useMutation({
    mutationFn: async ({ text, source }: AppendNotePayload) => {
      try {
        return await tools.appendNote(
          {
            text,
            target_type: targetType,
            target_id: targetId ?? undefined,
            source: source ?? undefined
          },
          token
        );
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: qk.notesScope(targetType, targetId)
      });
    }
  });
}

export interface UseDeleteNoteArgs {
  targetType?: NoteTargetType;
  targetId?: UUID | null;
}

/**
 * Delete a note by id. Same scoped invalidation as `useAppendNote`.
 * Backend enforces the rule — frontend just gates the UI so non-author users
 * never see the affordance.
 */
export function useDeleteNote({ targetType = "user", targetId = null }: UseDeleteNoteArgs = {}) {
  const auth = useAuth();
  const token = auth.user?.access_token ?? "";
  const queryClient = useQueryClient();
  const qk = useQueryKeys();

  return useMutation({
    mutationFn: async ({ noteId }: { noteId: UUID }) => {
      try {
        return await tools.deleteNote({ note_id: noteId }, token);
      } catch (error) {
        if (error instanceof UnauthorizedError) void auth.signinRedirect();
        throw error;
      }
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: qk.notesScope(targetType, targetId)
      });
    }
  });
}
