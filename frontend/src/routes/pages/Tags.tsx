import { Link } from "@tanstack/react-router";
import { Hash, Tags as TagsIcon, TrendingUp } from "lucide-react";
import { useMemo } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StatStrip, StatTile, StatTileSkeleton } from "@/components/domain/Stats";
import { useTags } from "@/hooks/useMcp";
import { cn } from "@/lib/utils";

const maxUsage = (items: Array<{ usage_count?: number }>) =>
  Math.max(...items.map((t) => t.usage_count ?? 0), 1);

/** Usage-weighted type scale for the tag cloud. */
function tagSize(usage: number, max: number): string {
  const ratio = usage / max;
  if (ratio > 0.75) return "text-lg";
  if (ratio > 0.5) return "text-base";
  if (ratio > 0.25) return "text-sm";
  return "text-xs";
}

export function TagsRoute() {
  const tags = useTags();
  const items = useMemo(() => tags.data?.items ?? [], [tags.data]);

  const totalUsage = useMemo(() => items.reduce((sum, t) => sum + (t.usage_count ?? 0), 0), [items]);
  const topTag = useMemo(
    () => items.reduce<{ name: string; n: number } | null>((best, t) => {
      const n = t.usage_count ?? 0;
      if (!best || n > best.n) return { name: t.display_name ?? t.slug, n };
      return best;
    }, null),
    [items]
  );

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2.5">
          <h1 className="text-2xl font-semibold tracking-tight">Tags</h1>
          {!tags.isLoading && tags.data ? (
            <span className="co-mono-numeric rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {items.length}
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">Browse tenant tags and jump into filtered people views.</p>
      </div>

      {/* Overview stat strip */}
      {tags.isLoading ? (
        <StatStrip>
          <StatTileSkeleton />
          <StatTileSkeleton />
          <StatTileSkeleton />
        </StatStrip>
      ) : items.length ? (
        <StatStrip>
          <StatTile label="Tags" value={items.length} icon={TagsIcon} tone="amber" />
          <StatTile label="Total applications" value={totalUsage} icon={Hash} tone="sky" hint="tag-to-person links" />
          <StatTile
            label="Most used"
            value={topTag?.n ?? 0}
            icon={TrendingUp}
            tone="emerald"
            hint={topTag ? topTag.name : "no tags applied"}
          />
        </StatStrip>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TagsIcon className="h-4 w-4 text-[oklch(var(--co-amber-500))]" strokeWidth={1.8} />
            Tag cloud
          </CardTitle>
          <CardDescription>Sized by usage. Select a tag to filter the people list.</CardDescription>
        </CardHeader>
        <CardContent>
          {tags.isLoading ? (
            <div className="flex flex-wrap gap-2">
              {Array.from({ length: 10 }).map((_, i) => (
                <Skeleton key={i} className="h-7 w-20 rounded-full" />
              ))}
            </div>
          ) : items.length ? (
            <div className="flex flex-wrap items-center gap-2">
              {(() => {
                const max = maxUsage(items);
                return items.map((tag) => (
                  <Link
                    key={tag.slug}
                    to="/people"
                    search={{ tag: tag.slug } as never}
                    aria-label={`Filter people by ${tag.display_name ?? tag.slug}`}
                    className={cn(
                      "focus-ring group inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/70 px-3 py-1 font-medium text-foreground/80 transition-colors hover:border-primary/40 hover:text-foreground",
                      tagSize(tag.usage_count ?? 0, max)
                    )}
                  >
                    <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
                    <span className="truncate">{tag.display_name ?? tag.slug}</span>
                    {tag.usage_count != null ? (
                      <span className="co-mono-numeric ml-0.5 text-xs text-muted-foreground">{tag.usage_count}</span>
                    ) : null}
                  </Link>
                ));
              })()}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4 py-12 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
                <TagsIcon className="h-6 w-6" strokeWidth={1.5} />
              </div>
              <div className="space-y-1">
                <h2 className="text-lg font-semibold">No tags yet</h2>
                <p className="mx-auto max-w-md text-sm text-muted-foreground">
                  Tags appear here once people are tagged in the system.
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
