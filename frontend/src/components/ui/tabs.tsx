import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = TabsPrimitive.Root;

export const TabsList = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>) => (
  <TabsPrimitive.List className={cn("inline-flex h-9 items-center rounded-md bg-muted p-1", className)} {...props} />
);

export const TabsTrigger = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>) => (
  <TabsPrimitive.Trigger
    className={cn(
      "focus-ring inline-flex h-7 items-center justify-center rounded px-3 text-sm font-medium text-muted-foreground data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm",
      className
    )}
    {...props}
  />
);

export const TabsContent = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>) => (
  <TabsPrimitive.Content className={cn("mt-4", className)} {...props} />
);
