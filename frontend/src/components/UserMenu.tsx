import {
  DropDownMenu,
  DropDownTrigger,
  DropDownContent,
  DropDownItem,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "react-oidc-context";
import { Settings, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";

export function UserMenu() {
  const { user, isAuthenticated, isLoading, signoutRedirect } = useAuth();

  if (isLoading) {
    return (
      <Button variant="outline" size="icon" className="h-9 w-9" disabled>
        <Avatar className="h-8 w-8">
          <AvatarFallback>...</AvatarFallback>
        </Avatar>
      </Button>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <Button variant="outline" size="icon" className="h-9 w-9" disabled>
        <Avatar className="h-8 w-8">
          <AvatarFallback>?</AvatarFallback>
        </Avatar>
      </Button>
    );
  }

  const profile = user.profile as Record<string, unknown> | undefined;
  const name = String(profile?.name ?? profile?.preferred_username ?? profile?.email ?? "User");
  const email = String(profile?.email ?? "");
  const ucUid = profile?.uc_uid ? String(profile.uc_uid) : undefined;
  const picture = profile?.picture ? String(profile.picture) : "";
  const initials = name.charAt(0).toUpperCase();

  return (
    <DropDownMenu>
      <DropDownTrigger asChild>
        <Button variant="outline" size="icon" className="h-9 w-9">
          <Avatar className="h-8 w-8">
            <AvatarImage src={picture} />
            <AvatarFallback className="bg-primary text-primary-foreground text-sm font-medium">
              {initials}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropDownTrigger>
      <DropDownContent
        className="w-56 p-0 rounded-md border bg-popover shadow-md"
        sideOffset={6}
        align="end"
      >
        <div className="space-y-1">
          <div className="flex items-center space-x-3 p-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary font-semibold">
              {initials}
            </div>
            <div className="min-w-0">
              <p className="font-medium truncate">{name}</p>
              {email ? (
                <p className="text-xs text-muted-foreground truncate">{email}</p>
              ) : null}
              {ucUid ? (
                <p className="text-xs text-muted-foreground font-mono truncate">
                  {ucUid}
                </p>
              ) : null}
            </div>
          </div>
          <div className="h-px bg-muted" />
          <DropDownItem className="flex w-full items-center px-3 py-2 text-sm cursor-pointer rounded-sm outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground">
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </DropDownItem>
          <DropDownItem
            onSelect={() => {
              void signoutRedirect();
            }}
            className="flex w-full items-center px-3 py-2 text-sm cursor-pointer rounded-sm outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground"
          >
            <LogOut className="mr-2 h-4 w-4" />
            Sign out
          </DropDownItem>
        </div>
      </DropDownContent>
    </DropDownMenu>
  );
}