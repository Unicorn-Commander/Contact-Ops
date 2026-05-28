/**
 * ChatInput — the composer at the bottom of the AI Analyst panel.
 *
 * Single autosizing textarea (capped at ~6 lines, then scrolls). Keyboard:
 *   - Enter        send
 *   - Shift+Enter  newline
 *   - ⌘/Ctrl+Enter alt-send (same effect, supported for muscle memory)
 *
 * The composer is intentionally dumb: it doesn't own the message list. It just
 * collects text and calls `onSend`. The panel owns sending + cancel state.
 */
import { ArrowUp, Loader2, Square } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface ChatInputProps {
  onSend: (content: string) => void;
  /** Optional cancel for the in-flight stream (Esc-like inline button). */
  onCancel?: () => void;
  /** "sending" or "streaming" — disables send, shows the cancel/spinner state. */
  busy?: boolean;
  /** Pre-filled value (used by suggested prompts in the empty state). */
  initialValue?: string;
  /** Auto-focus on mount (we want focus when the panel opens). */
  autoFocus?: boolean;
  placeholder?: string;
}

const MAX_LINES = 6;

export function ChatInput({
  onSend,
  onCancel,
  busy = false,
  initialValue = "",
  autoFocus = false,
  placeholder = "Ask about people, orgs, tags, or recent changes…"
}: ChatInputProps) {
  const [value, setValue] = useState(initialValue);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Sync external pre-fill (suggested prompt clicks).
  useEffect(() => {
    if (initialValue && initialValue !== value) {
      setValue(initialValue);
      // Move caret to end so the user can keep typing immediately.
      window.requestAnimationFrame(() => {
        const node = textareaRef.current;
        if (node) {
          node.focus();
          node.setSelectionRange(initialValue.length, initialValue.length);
        }
      });
    }
    // We intentionally watch only initialValue — local typing must not refire this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialValue]);

  // Autosize. `useLayoutEffect` so it sizes before paint, no flash.
  useLayoutEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    const lineHeight = parseFloat(window.getComputedStyle(node).lineHeight || "20");
    const maxHeight = lineHeight * MAX_LINES + 16; // + textarea padding
    const next = Math.min(node.scrollHeight, maxHeight);
    node.style.height = `${next}px`;
    node.style.overflowY = node.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || busy) return;
    onSend(text);
    setValue("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // ⌘↵ / Ctrl+↵ always sends, regardless of Shift.
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
      return;
    }
    // Plain Enter sends; Shift+Enter inserts a newline.
    if (event.key === "Enter" && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="border-t border-border bg-card/80 p-3 backdrop-blur"
    >
      <div className="relative">
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          autoFocus={autoFocus}
          placeholder={placeholder}
          aria-label="Message the AI Analyst"
          rows={1}
          className={cn(
            "min-h-[40px] resize-none rounded-lg border bg-background pr-10 text-sm leading-snug",
            "shadow-[var(--shadow-1)] transition-shadow focus:shadow-[var(--shadow-2)]"
          )}
        />
        {busy && onCancel ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="absolute bottom-1 right-1 h-7 w-7 rounded-md text-muted-foreground hover:text-foreground"
            onClick={onCancel}
            aria-label="Stop generating"
            title="Stop generating"
          >
            <Square className="h-3.5 w-3.5" strokeWidth={2} />
          </Button>
        ) : (
          <Button
            type="submit"
            variant="gradient"
            size="icon"
            className="absolute bottom-1 right-1 h-7 w-7 rounded-md"
            disabled={!value.trim() || busy}
            aria-label="Send message"
            title="Send (Enter)"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
            ) : (
              <ArrowUp className="h-3.5 w-3.5" strokeWidth={2} />
            )}
          </Button>
        )}
      </div>
      <p className="mt-1 text-[10.5px] leading-tight text-muted-foreground">
        <span className="font-mono">Enter</span> to send · <span className="font-mono">Shift+Enter</span> newline ·{" "}
        <span className="font-mono">Esc</span> closes
      </p>
    </form>
  );
}
