import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useTags } from "@/hooks/useMcp";

const maxUsage = (items: Array<{ usage_count?: number }>) =>
  Math.max(...items.map((t) => t.usage_count ?? 0), 1);

function TagSize(usage: number, max: number): string {
  const ratio = usage / max;
  if (ratio > 0.75) return "text-lg";
  if (ratio > 0.5) return "text-base";
  if (ratio > 0.25) return "text-sm";
  return "text-xs";
}

export function TagsRoute() {
  const tags = useTags();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Tags</h1>
        <p className="text-sm text-muted-foreground">Browse tenant tags and jump into filtered people views.</p>
      </div>
      <Card className="border-violet-200">
        <CardHeader>
          <CardTitle className="text-violet-600">Tag cloud</CardTitle>
          <CardDescription>Click a tag to filter the people list.</CardDescription>
        </CardHeader>
        <CardContent>
          {tags.isLoading ? (
            <div className="flex flex-wrap gap-2">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-6 w-20 rounded-full" />
              ))}
            </div>
          ) : tags.data?.items?.length ? (
            <div className="flex flex-wrap gap-2">
              {(() => {
                const max = maxUsage(tags.data!.items);
                return tags.data!.items.map((tag) => (
                  <Link key={tag.slug} to="/people" search={{ tag: tag.slug } as never}>
                    <Badge
                      variant="secondary"
                      className={`${TagSize(tag.usage_count ?? 0, max)} border-indigo-200 bg-indigo-50 text-indigo-700 dark:border-indigo-900 dark:bg-indigo-950 dark:text-indigo-200`}
                    >
                      {tag.display_name ?? tag.slug}
                      {tag.usage_count != null && (
                        <span className="ml-1 text-muted-foreground">
                          {tag.usage_count}
                        </span>
                      )}
                    </Badge>
                  </Link>
                ));
              })()}
            </div>
          ) : (
            <div className="text-center py-8">
              <p className="text-sm text-muted-foreground">No tags have been applied yet.</p>
              <p className="text-xs text-muted-foreground mt-1">Tags appear here once people are tagged in the system.</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
