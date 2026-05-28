/**
 * MessageBubble — one rendered turn in the AI Analyst thread.
 *
 * User turns are right-aligned brand-tinted; assistant turns are left-aligned
 * surface cards. While the assistant is mid-stream, a small blinking cursor
 * (matches the Streaming primitive in `design-system/Streaming.tsx`) sits at
 * the end of the in-progress text.
 *
 * Tool-call rendering is delegated to <ToolCallCard /> so this file stays a
 * pure presentational piece; the panel composes them inline between
 * assistant-text fragments.
 */
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/hooks/useChat";
import { ToolCallCard } from "@/components/chat/ToolCallCard";
import { Sparkles, AlertCircle } from "lucide-react";

export interface MessageBubbleProps {
  message: ChatMessage;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  if (message.role === "user") {
    return (
      <li className="flex justify-end" aria-label="You said">
        <div
          className={cn(
            "max-w-[85%] rounded-2xl rounded-tr-sm px-3 py-2 text-sm leading-snug shadow-[var(--shadow-1)]",
            "bg-primary text-primary-foreground"
          )}
        >
          {message.content}
        </div>
      </li>
    );
  }

  if (message.role === "tool") {
    // Reserved for future surfaced-tool-output blocks; today the panel renders
    // tool calls inline on the assistant message, so this branch is just a
    // safety net for forward-compat.
    return null;
  }

  // Assistant turn.
  const hasContent = message.content.length > 0;
  const toolCalls = message.tool_calls ?? [];
  const showCursor = Boolean(message.streaming);

  return (
    <li className="flex justify-start" aria-label="AI Analyst said">
      <div
        className={cn(
          "max-w-[92%] space-y-2 rounded-2xl rounded-tl-sm border border-border bg-card px-3 py-2.5 text-sm leading-snug text-card-foreground shadow-[var(--shadow-1)]",
          message.error && "border-[oklch(var(--co-rose-500)/0.5)] bg-[oklch(var(--co-rose-500)/0.06)]"
        )}
      >
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
          <Sparkles className="h-3 w-3 text-[oklch(var(--co-brand-500))]" strokeWidth={1.8} />
          AI Analyst
        </div>

        {hasContent ? (
          <p className="whitespace-pre-wrap break-words">
            {message.content}
            {showCursor && toolCalls.length === 0 ? (
              <span
                className="co-streaming-cursor ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 rounded-full bg-[oklch(var(--primary))] align-middle"
                aria-hidden="true"
              />
            ) : null}
          </p>
        ) : null}

        {toolCalls.length > 0 ? (
          <div className="space-y-1.5">
            {toolCalls.map((call) => (
              <ToolCallCard key={call.call_id} call={call} />
            ))}
          </div>
        ) : null}

        {showCursor && !hasContent && toolCalls.length === 0 ? (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="co-animate-pulse-subtle inline-block h-1.5 w-1.5 rounded-full bg-[oklch(var(--primary))]" />
            Thinking…
          </p>
        ) : null}

        {message.error ? (
          <p className="flex items-start gap-1.5 text-xs text-[oklch(var(--co-rose-500))]">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.8} />
            <span>{message.error}</span>
          </p>
        ) : null}
      </div>
    </li>
  );
}
