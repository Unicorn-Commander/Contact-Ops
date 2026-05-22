import * as ToastPrimitive from "@radix-ui/react-toast";
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

type ToastMessage = { id: string; title: string; description?: string; variant?: "default" | "destructive" };
type ToastContextValue = { toast: (message: Omit<ToastMessage, "id">) => void };
const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast() {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast must be used inside ToastProvider");
  return value;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = useState<ToastMessage[]>([]);
  const toast = useCallback((message: Omit<ToastMessage, "id">) => {
    setMessages((current) => [...current, { ...message, id: crypto.randomUUID() }]);
  }, []);
  const value = useMemo(() => ({ toast }), [toast]);
  return (
    <ToastContext.Provider value={value}>
      <ToastPrimitive.Provider swipeDirection="right">
        {children}
        {messages.map((message) => (
          <ToastPrimitive.Root
            key={message.id}
            className={cn(
              "rounded-lg border bg-card p-4 text-card-foreground shadow-lg",
              message.variant === "destructive" && "border-destructive"
            )}
            onOpenChange={(open) => {
              if (!open) setMessages((current) => current.filter((item) => item.id !== message.id));
            }}
          >
            <ToastPrimitive.Title className="text-sm font-semibold">{message.title}</ToastPrimitive.Title>
            {message.description ? (
              <ToastPrimitive.Description className="mt-1 text-sm text-muted-foreground">
                {message.description}
              </ToastPrimitive.Description>
            ) : null}
          </ToastPrimitive.Root>
        ))}
        <ToastPrimitive.Viewport className="fixed bottom-4 right-4 z-50 flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2" />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  );
}
