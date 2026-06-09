import { useMemo, useState } from "react";
import { ExternalLink, Send, Hand, AlertCircle } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Select } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { usePublishedHistory } from "@/lib/api";
import { timeAgo, channelLabel, cn } from "@/lib/utils";
import type { PublishedItem } from "@/lib/types";

// Publish history — a durable record of everything that left the queue for an
// external platform. The Approval Queue only shows pending/in-flight items, so
// without this view there was no way to see what had actually shipped.
export default function PublishedPage() {
  const [channel, setChannel] = useState("");
  const { data, isLoading, isError, refetch } = usePublishedHistory(channel || undefined);

  const channels = useMemo(() => {
    const s = new Set<string>();
    for (const r of data ?? []) if (r.channel) s.add(r.channel);
    return Array.from(s);
  }, [data]);

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Published"
        description="Everything that shipped to an external platform — Dev.to, Substack, LinkedIn, and ad networks — newest first."
      >
        <Select value={channel} onChange={(e) => setChannel(e.target.value)} className="w-44">
          <option value="">All channels</option>
          {channels.map((c) => (
            <option key={c} value={c}>{channelLabel(c)}</option>
          ))}
        </Select>
      </PageHeader>

      <div className="p-8 max-w-5xl">
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-14" /><Skeleton className="h-14" /><Skeleton className="h-14" />
          </div>
        )}
        {!isLoading && isError && <ErrorState what="publish history" onRetry={() => refetch()} />}
        {!isLoading && !isError && (data?.length ?? 0) === 0 && (
          <Empty
            icon={Send}
            title="Nothing published yet"
            description="Approve a draft in the Queue and, once it ships to its platform, it'll appear here with a link to the live post."
          />
        )}
        {!isLoading && !isError && (data?.length ?? 0) > 0 && (
          <ul className="divide-y divide-border rounded-lg border bg-card">
            {data!.map((r) => <PublishedRow key={r.telemetry_id} item={r} />)}
          </ul>
        )}
      </div>
    </div>
  );
}

function PublishedRow({ item }: { item: PublishedItem }) {
  const manual = item.publish_mode === "manual_review" || item.publish_state === "manual_review";
  const failed = item.publish_state === "failed";
  const title = item.title?.trim() || "(untitled draft)";

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{title}</span>
          {item.platform && (
            <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
              {item.platform}
            </span>
          )}
          {item.channel && (
            <span className="shrink-0 text-[11px] text-muted-foreground">{channelLabel(item.channel)}</span>
          )}
        </div>
        <div className="mt-0.5 text-[11px] text-muted-foreground">
          {item.published_at ? timeAgo(item.published_at) : "—"}
        </div>
      </div>

      <StatusBadge manual={manual} failed={failed} />

      {item.external_url ? (
        <a
          href={item.external_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex shrink-0 items-center gap-1 rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted"
        >
          View <ExternalLink className="h-3 w-3" />
        </a>
      ) : (
        <span className="shrink-0 text-[11px] text-muted-foreground">no link</span>
      )}
    </li>
  );
}

function StatusBadge({ manual, failed }: { manual: boolean; failed: boolean }) {
  if (failed) {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-destructive/10 px-2 py-0.5 text-[11px] font-medium text-destructive">
        <AlertCircle className="h-3 w-3" /> Failed
      </span>
    );
  }
  if (manual) {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-foreground">
        <Hand className="h-3 w-3" /> Manual
      </span>
    );
  }
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full bg-success/10 px-2 py-0.5 text-[11px] font-medium text-success")}>
      <Send className="h-3 w-3" /> Published
    </span>
  );
}
