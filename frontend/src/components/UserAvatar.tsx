import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { cn, initials } from "@/lib/utils";

export function UserAvatar({
  name,
  picture,
  className,
  fallbackClassName
}: {
  name: string;
  picture?: string;
  className?: string;
  fallbackClassName?: string;
}) {
  return (
    <Avatar className={className}>
      {picture ? <AvatarImage src={picture} alt={`${name} avatar`} /> : null}
      <AvatarFallback className={cn("bg-primary text-primary-foreground text-sm font-medium", fallbackClassName)}>
        {initials(name)}
      </AvatarFallback>
    </Avatar>
  );
}
