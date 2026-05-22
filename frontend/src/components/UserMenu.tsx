import { DropDownTrigger, DropDownContent, DropDownItem } from "@/components/ui/dropdown-menu";
import { useAuth } from "react-oidc-context";
import { Settings, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";

export function UserMenu() {
  const { user, isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <DropDownTrigger asChild>
        <Button variant="outline" size="icon" className="h-9 w-9">
          <Avatar className="h-8 w-8">
            <AvatarFallback>...</AvatarFallback>
          </Avatar>
        </Button>
      </DropDownTrigger>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <DropDownTrigger asChild>
        <Button variant="outline" size="icon" className="h-9 w-9">
          <Avatar className="h-8 w-8">
            <AvatarFallback>?</AvatarFallback>
          </Avatar>
        </Button>
      </DropDownTrigger>
    );
  }

  const profile = user.profile as Record<string, unknown> | undefined;
  const name = String(profile?.name ?? profile?.preferred_username ?? profile?.email ?? "User");
  const email = String(profile?.email ?? "");
  const ucUid = profile?.uc_uid ? String(profile.uc_uid) : undefined;
  const picture = profile?.picture ? String(profile.picture) : "";
  const initials = name.charAt(0).toUpperCase();

  return (
    <>
      <DropDownTrigger asChild>
        <Button variant="outline" size="icon" className="h-9 w-9">
          <Avatar className="h-8 w-8">
            <AvatarImage src={picture} />
            <AvatarFallback>{initials}</AvatarFallback>
          </Avatar>
        </Button>
      </DropDownTrigger>
      <DropDownContent className="w-48 p-0" sideOffset={4} align="end">
        <div className="space-y-1">
          <div className="flex items-center space-x-3 p-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-violet-500/20 text-violet-500">
              {initials}
            </div>
            <div>
              <p className="font-medium">{name}</p>
              <p className="text-xs text-muted-foreground">
                {email}
              </p>
              {ucUid && (
                <p className="text-xs text-muted-foreground font-mono">
                  UCID: {ucUid}
                </p>
              )}
            </div>
          </div>
          <div className="h-px bg-muted"></div>
          <DropDownItem>
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </DropDownItem>
          <DropDownItem>
            <LogOut className="mr-2 h-4 w-4" />
            Sign out
          </DropDownItem>
        </div>
      </DropDownContent>
    </>
  );
}