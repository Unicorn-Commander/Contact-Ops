import { Link } from "@tanstack/react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TagChip } from "@/components/domain/Badges";
import { useTags } from "@/hooks/useMcp";

export function TagsRoute() {
  const tags = useTags();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Tags</h1>
        <p className="text-sm text-muted-foreground">Browse tenant tags and jump into filtered people views.</p>
      </div>
      <Card>
        <CardHeader><CardTitle>Tag cloud</CardTitle></CardHeader>
        <CardContent>
          {tags.isLoading ? <Skeleton className="h-48 w-full" /> : tags.data?.items?.length ? (
            <div className="flex flex-wrap gap-2">
              {tags.data.items.map((tag) => (
                <Link key={tag.slug} to="/people" search={{ tag: tag.slug } as never}>
                  <TagChip label={`${tag.display_name ?? tag.slug}${tag.usage_count ? ` (${tag.usage_count})` : ""}`} />
                </Link>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">No tags have been applied yet.</p>}
        </CardContent>
      </Card>
    </div>
  );
}
