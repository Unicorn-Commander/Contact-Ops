import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        destructive: "border-transparent bg-destructive text-destructive-foreground",
        outline: "text-foreground",
        // Soft-tint semantic pills (low-opacity fill + accent text) — the
        // family's status-chip vocabulary.
        accent: "border-transparent bg-[oklch(var(--accent-fuchsia)/0.16)] text-[oklch(var(--accent-fuchsia))]",
        success: "border-transparent bg-[oklch(var(--success)/0.16)] text-[oklch(var(--success))]",
        warning: "border-transparent bg-[oklch(var(--warning)/0.18)] text-[oklch(var(--warning))]",
        info: "border-transparent bg-[oklch(var(--info)/0.16)] text-[oklch(var(--info))]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
