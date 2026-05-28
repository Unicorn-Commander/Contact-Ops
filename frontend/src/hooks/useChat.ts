/**
 * useChat — the AI Contacts Analyst stream driver.
 *
 * Owns the chat state machine on the frontend:
 *   - in-memory message list (no persistence; the backend may persist via
 *     `conversation_id` returned in the `done` event)
 *   - sending a user turn → opening an SSE POST → folding events into messages
 *   - cancellation (AbortController) so a `New chat` or unmount cuts the stream
 *
 * The backend speaks the contract documented in `lib/mcp.ts`:
 *   {event:"delta", content:"...token..."}
 *   {event:"tool_call", call_id, tool, args}
 *   {event:"tool_result", call_id, result|error}
 *   {event:"done", conversation_id}
 *   {event:"error", message}
 *
 * The hook is **transport-pluggable**: callers can pass an `openStream` factory
 * so the design-system preview can run on a mock generator without a backend.
 * In production the AIAnalystPanel passes a real `sseStream(...)` adapter
 * that uses the bearer token from react-oidc-context.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SSEEvent } from "@/lib/mcp";

// ---- Domain types ---------------------------------------------------------

export type ChatRole = "user" | "assistant" | "tool";

export type ToolCall = {
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
  /** Filled by a matching `tool_result` event. `null` while pending. */
  result?: unknown;
  /** Set if the backend reported a tool error. */
  error?: string;
};

export type ChatMessage = {
  /** Stable client id, distinct from the backend's call_ids. */
  id: string;
  role: ChatRole;
  /** Streaming assistant text accumulates here token-by-token. */
  content: string;
  /** Tool calls inlined with the assistant turn that issued them. */
  tool_calls?: ToolCall[];
  /** True while the assistant is still streaming this turn. */
  streaming?: boolean;
  /** Filled with an error message if the turn ended in `event:"error"`. */
  error?: string;
  createdAt: number;
};

export type ChatStatus = "idle" | "sending" | "streaming" | "error";

export type OpenChatStream = (
  payload: { messages: Array<{ role: ChatRole; content: string }>; conversation_id: string | null },
  signal: AbortSignal
) => AsyncIterable<SSEEvent>;

export type UseChatOptions = {
  /** Override the transport — defaults to the real backend SSE. */
  openStream: OpenChatStream;
  /** Optional initial message list (used by the showcase preview). */
  initialMessages?: ChatMessage[];
};

export type UseChatReturn = {
  messages: ChatMessage[];
  status: ChatStatus;
  conversationId: string | null;
  error: string | null;
  send: (content: string) => void;
  reset: () => void;
  cancel: () => void;
};

// ---- Small helpers --------------------------------------------------------

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `msg_${Math.random().toString(36).slice(2)}_${Date.now()}`;
}

// ---- Reducer-style updaters ----------------------------------------------

function appendDelta(messages: ChatMessage[], delta: string): ChatMessage[] {
  // Append the token to the active streaming assistant message (last one).
  // If there isn't one (the backend started with a tool_call), create it.
  const last = messages[messages.length - 1];
  if (last && last.role === "assistant" && last.streaming) {
    const updated: ChatMessage = { ...last, content: last.content + delta };
    return [...messages.slice(0, -1), updated];
  }
  return [
    ...messages,
    {
      id: newId(),
      role: "assistant",
      content: delta,
      streaming: true,
      createdAt: Date.now()
    }
  ];
}

function attachToolCall(messages: ChatMessage[], call: ToolCall): ChatMessage[] {
  // Attach the tool call to the active assistant message; create one if missing.
  const last = messages[messages.length - 1];
  if (last && last.role === "assistant" && last.streaming) {
    const updated: ChatMessage = {
      ...last,
      tool_calls: [...(last.tool_calls ?? []), call]
    };
    return [...messages.slice(0, -1), updated];
  }
  return [
    ...messages,
    {
      id: newId(),
      role: "assistant",
      content: "",
      tool_calls: [call],
      streaming: true,
      createdAt: Date.now()
    }
  ];
}

function resolveToolCall(
  messages: ChatMessage[],
  call_id: string,
  patch: Pick<ToolCall, "result" | "error">
): ChatMessage[] {
  // Walk messages tail-first; the matching call is almost always on the last
  // assistant turn, but `id` matching across turns is the safe rule.
  return messages.map((message) => {
    if (!message.tool_calls?.length) return message;
    let touched = false;
    const next = message.tool_calls.map((call) => {
      if (call.call_id !== call_id) return call;
      touched = true;
      return { ...call, ...patch };
    });
    return touched ? { ...message, tool_calls: next } : message;
  });
}

function finishStreaming(messages: ChatMessage[], errorMessage?: string): ChatMessage[] {
  const last = messages[messages.length - 1];
  if (!last || !last.streaming) return messages;
  return [
    ...messages.slice(0, -1),
    { ...last, streaming: false, error: errorMessage ?? last.error }
  ];
}

// ---- Hook -----------------------------------------------------------------

export function useChat({ openStream, initialMessages }: UseChatOptions): UseChatReturn {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages ?? []);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Cancel any in-flight stream on unmount.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus((prev) => (prev === "sending" || prev === "streaming" ? "idle" : prev));
    setMessages((prev) => finishStreaming(prev));
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setStatus("idle");
    setConversationId(null);
    setError(null);
  }, []);

  const send = useCallback(
    (content: string) => {
      const text = content.trim();
      if (!text) return;
      if (status === "sending" || status === "streaming") return;

      // Optimistic user message.
      const userMessage: ChatMessage = {
        id: newId(),
        role: "user",
        content: text,
        createdAt: Date.now()
      };

      // Build the transport payload from the user-visible history. Tool
      // messages are filtered out — the backend reconstructs them from the
      // active `conversation_id` (it owns the canonical tool transcript).
      setMessages((prev) => {
        const nextMessages = [...prev, userMessage];
        const payload = {
          messages: nextMessages
            .filter((message) => message.role === "user" || message.role === "assistant")
            .map((message) => ({ role: message.role, content: message.content })),
          conversation_id: conversationId
        };

        // Kick off the stream in a microtask so the optimistic message paints
        // first (avoids a perceived input-lag flash).
        void Promise.resolve().then(async () => {
          setStatus("sending");
          setError(null);
          const controller = new AbortController();
          abortRef.current = controller;

          try {
            const stream = openStream(payload, controller.signal);
            let started = false;
            for await (const eventPayload of stream) {
              if (controller.signal.aborted) break;
              if (!started) {
                started = true;
                setStatus("streaming");
              }

              switch (eventPayload.event) {
                case "delta": {
                  const token = typeof eventPayload.content === "string" ? eventPayload.content : "";
                  if (token) setMessages((messageList) => appendDelta(messageList, token));
                  break;
                }
                case "tool_call": {
                  const call: ToolCall = {
                    call_id: String(eventPayload.call_id ?? newId()),
                    tool: String(eventPayload.tool ?? "unknown_tool"),
                    args:
                      eventPayload.args && typeof eventPayload.args === "object"
                        ? (eventPayload.args as Record<string, unknown>)
                        : {}
                  };
                  setMessages((messageList) => attachToolCall(messageList, call));
                  break;
                }
                case "tool_result": {
                  const callId = String(eventPayload.call_id ?? "");
                  const errMessage =
                    typeof eventPayload.error === "string"
                      ? eventPayload.error
                      : undefined;
                  setMessages((messageList) =>
                    resolveToolCall(messageList, callId, {
                      result: errMessage ? undefined : eventPayload.result,
                      error: errMessage
                    })
                  );
                  break;
                }
                case "done": {
                  const nextConversation =
                    typeof eventPayload.conversation_id === "string"
                      ? eventPayload.conversation_id
                      : null;
                  if (nextConversation) setConversationId(nextConversation);
                  setMessages((messageList) => finishStreaming(messageList));
                  setStatus("idle");
                  break;
                }
                case "error": {
                  const message =
                    typeof eventPayload.message === "string"
                      ? eventPayload.message
                      : "The assistant stream ended unexpectedly.";
                  setError(message);
                  setMessages((messageList) => finishStreaming(messageList, message));
                  setStatus("error");
                  break;
                }
                default:
                  // Unknown event — ignore (forwards-compatible with backend additions).
                  break;
              }
            }

            // Stream closed without explicit `done`/`error`: clean up the
            // streaming flag and return to idle (the message keeps whatever
            // content it has). If we never received a single event AND we
            // weren't aborted, surface that as an error.
            if (controller.signal.aborted) return;
            setMessages((messageList) => finishStreaming(messageList));
            setStatus((prev) => (prev === "error" ? prev : "idle"));
            if (!started) {
              setError("No response from the AI Analyst.");
              setStatus("error");
            }
          } catch (caught) {
            if (controller.signal.aborted) return;
            const message =
              caught instanceof Error ? caught.message : "Unknown error talking to the assistant.";
            setError(message);
            setMessages((messageList) => finishStreaming(messageList, message));
            setStatus("error");
          } finally {
            if (abortRef.current === controller) abortRef.current = null;
          }
        });

        return nextMessages;
      });
    },
    [conversationId, openStream, status]
  );

  return useMemo(
    () => ({ messages, status, conversationId, error, send, reset, cancel }),
    [messages, status, conversationId, error, send, reset, cancel]
  );
}
