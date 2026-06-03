import { useCallback, useEffect, useState } from "react";
import {
  DEFAULT_LOCAL_MODEL,
  disposeActiveEngine,
  getActiveEngine,
  loadLocalLLM,
  setActiveEngine,
  subscribeActiveEngine,
  type ChatMessage,
  type LocalLLMEngine,
  type ModelStatus
} from "@/lib/ondevice/llm";
import type { LocalContact } from "@/lib/ondevice/db";

function contactSummaryText(contact: LocalContact): string {
  return [
    contact.name,
    contact.title,
    contact.org,
    contact.emails.join(", "),
    contact.phones.join(", "),
    contact.notes
  ]
    .filter(Boolean)
    .join(" - ");
}

export function useOndeviceLLM() {
  const [engine, setEngine] = useState<LocalLLMEngine | null>(() => getActiveEngine());
  const [status, setStatus] = useState<ModelStatus>(() =>
    engine ? { status: "ready", modelId: engine.modelId } : { status: "idle" }
  );

  useEffect(() => subscribeActiveEngine(setEngine), []);

  useEffect(() => {
    if (engine) setStatus({ status: "ready", modelId: engine.modelId });
  }, [engine]);

  const load = useCallback(async (modelId = DEFAULT_LOCAL_MODEL) => {
    const existing = getActiveEngine();
    if (existing?.modelId === modelId) {
      setStatus({ status: "ready", modelId });
      return existing;
    }

    setStatus({ status: "loading", progress: 0, text: "Initializing model..." });
    await disposeActiveEngine();
    try {
      const loaded = await loadLocalLLM(modelId, (progress) => {
        setStatus({
          status: "loading",
          progress: progress.progress ?? 0,
          text: progress.text
        });
      });
      setActiveEngine(loaded);
      setStatus({ status: "ready", modelId });
      return loaded;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setStatus({ status: "error", error: message });
      throw error;
    }
  }, []);

  const unload = useCallback(async () => {
    await disposeActiveEngine();
    setStatus({ status: "idle" });
  }, []);

  const chat = useCallback(
    async (messages: ChatMessage[], onDelta: (delta: string) => void, opts?: { signal?: AbortSignal }) => {
      const active = getActiveEngine();
      if (!active) throw new Error("Load an on-device model first.");
      await active.chat(messages, {
        onDelta,
        maxTokens: 900,
        temperature: 0.2,
        signal: opts?.signal
      });
    },
    []
  );

  const summarize = useCallback(async (contact: LocalContact) => {
    const active = getActiveEngine();
    if (!active) throw new Error("Load an on-device model first.");
    return active.generate(
      [
        {
          role: "system",
          content:
            "Summarize this contact for a relationship operator. Be concise and include missing-data gaps."
        },
        { role: "user", content: contactSummaryText(contact) }
      ],
      { maxTokens: 220, temperature: 0.1 }
    );
  }, []);

  return { status, engine, load, unload, chat, summarize };
}
