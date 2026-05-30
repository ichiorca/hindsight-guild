import { useMemo, useState } from "react";
import { Quote, Search, MessageSquare, Database, Sparkles, Copy } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Input, Select } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { Empty } from "@/components/ui/Empty";
import { ErrorState } from "@/components/ui/ErrorState";
import { useVoice } from "@/lib/api";
import { toast } from "@/lib/toast";
import { ICP_LABELS, icpLabel } from "@/lib/utils";

export default function VoicePage() {
  const [icp, setIcp] = useState("");
  const [query, setQuery] = useState("");
  const { data, isLoading, isError, refetch } = useVoice(icp || undefined);

  const filtered = useMemo(() => {
    if (!data) return [];
    if (!query) return data;
    const q = query.toLowerCase();
    return data.filter((v) =>
      v.text.toLowerCase().includes(q) ||
      v.theme?.toLowerCase().includes(q),
    );
  }, [data, query]);

  const byTheme = useMemo(() => {
    const m = new Map<string, typeof filtered>();
    for (const v of filtered) {
      const t = v.theme || "uncategorized";
      if (!m.has(t)) m.set(t, []);
      m.get(t)!.push(v);
    }
    return m;
  }, [filtered]);

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Customer Voice"
        description="What customers actually say — searchable by the team and pulled into every draft for grounding."
      >
        <Select value={icp} onChange={(e) => setIcp(e.target.value)} className="w-44">
          <option value="">All ICPs</option>
          {Object.entries(ICP_LABELS).map(([slug, label]) => (
            <option key={slug} value={slug}>{label}</option>
          ))}
        </Select>
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search quotes..."
            className="pl-9 w-64"
          />
        </div>
      </PageHeader>

      <div className="p-8 max-w-7xl">
        {isLoading && <div className="grid grid-cols-2 gap-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>}
        {!isLoading && isError && (
          <ErrorState what="customer voice" onRetry={() => refetch()} />
        )}
        {/* Empty-state branching:
             - Filter mismatch (data has rows, filtered is empty): suggest
               clearing filters.
             - Genuinely empty (no rows in customer_voice at all): show the
               Get-Started card explaining how to feed this collection. */}
        {!isLoading && !isError && (data?.length ?? 0) > 0 && filtered.length === 0 && (
          <Empty icon={Quote} title="No quotes match" description="Adjust the ICP filter or clear search." />
        )}
        {!isLoading && !isError && (data?.length ?? 0) === 0 && (
          <VoiceGetStarted />
        )}
        {!isLoading && filtered.length > 0 && (
          <div className="space-y-6">
            {Array.from(byTheme.entries()).map(([theme, quotes]) => (
              <section key={theme}>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
                  {theme} · {quotes.length}
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {quotes.map((v) => (
                    <Card key={v._id} className="group hover:shadow-md transition-shadow">
                      <CardContent className="p-4">
                        <p className="text-sm leading-relaxed">"{v.text}"</p>
                        <div className="flex gap-2 mt-3 items-center text-[11px]">
                          <Badge variant="muted">{icpLabel(v.icp_segment)}</Badge>
                          {v.persona && <span className="text-muted-foreground">{v.persona}</span>}
                          <span className="text-muted-foreground ml-auto">{v.source}</span>
                          <button
                            aria-label="Copy quote to clipboard"
                            title="Copy quote"
                            onClick={() => {
                              navigator.clipboard?.writeText(v.text).then(
                                () => toast.success("Quote copied", { description: "Paste it into a draft brief." }),
                                () => toast.error("Couldn't copy to clipboard"),
                              );
                            }}
                            className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                          >
                            <Copy className="h-3.5 w-3.5" /> Copy
                          </button>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Render when ``customer_voice`` is completely empty (not just filtered
 * empty). The user said "no guidance on how to make this page capture the
 * analytics or how this page is fed" — this card answers that directly,
 * step-by-step.
 */
function VoiceGetStarted() {
  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-primary" />
          Get started — your customer_voice collection is empty
        </CardTitle>
        <CardDescription>
          Customer Voice is the agentic team's window into how customers
          actually talk. Every agent pulls verbatim quotes from here when
          drafting copy or grading claims — so the more (and more specific)
          quotes you feed it, the sharper every downstream artifact gets.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5 text-sm">
        <div className="flex items-start gap-3">
          <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary text-[11px] font-semibold">1</span>
          <div>
            <p className="font-medium">Seed the demo data (fastest path)</p>
            <p className="text-muted-foreground mt-0.5">
              Run <code className="font-mono text-[12px] bg-subtle/60 px-1.5 py-0.5 rounded">python scripts/local_seed.py</code> to load
              <code className="font-mono text-[12px] bg-subtle/60 px-1.5 py-0.5 rounded mx-1">mongo/data/customer_voice.py</code>
              into local Mongo. Gives you ~40 quotes across 4 ICP segments.
            </p>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary text-[11px] font-semibold">2</span>
          <div>
            <p className="font-medium">Feed your real data via the Customer Voice agent</p>
            <p className="text-muted-foreground mt-0.5">
              The <code className="font-mono text-[12px] bg-subtle/60 px-1.5 py-0.5 rounded">customer_voice_agent</code> ingests raw
              text — sales-call transcripts, support tickets, NPS responses,
              churn interviews, community posts — and extracts the quotable
              moments. Each insertion is structured + auto-embedded by Voyage AI.
            </p>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary text-[11px] font-semibold">3</span>
          <div>
            <p className="font-medium">What a good quote looks like</p>
            <ul className="text-muted-foreground mt-1 space-y-1 text-[13px]">
              <li className="flex gap-2"><MessageSquare className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground/60" /><span><b>Specific</b> — names a tool, workflow, or outcome (not abstract).</span></li>
              <li className="flex gap-2"><MessageSquare className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground/60" /><span><b>Attributable</b> — sounds like an actual customer talking.</span></li>
              <li className="flex gap-2"><MessageSquare className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground/60" /><span><b>Insightful</b> — surfaces a non-obvious gnaw or delight, not "we like the product".</span></li>
            </ul>
          </div>
        </div>
        <div className="flex items-start gap-3">
          <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary text-[11px] font-semibold">4</span>
          <div>
            <p className="font-medium">Where it's used downstream</p>
            <p className="text-muted-foreground mt-0.5">
              Every Research → Content pipeline run pulls 3-5 ICP-matched
              quotes via vector search and the Content Agent embeds them
              verbatim. The Review Agent grades draft claims against this
              collection too. Filling it up directly improves the next draft.
            </p>
          </div>
        </div>
        <div className="rounded-md border border-info/30 bg-info/5 p-3 flex gap-2 items-start text-[12px]">
          <Database className="h-4 w-4 shrink-0 text-info mt-0.5" />
          <span className="text-muted-foreground">
            Collection: <code className="font-mono">customer_voice</code> in MongoDB.
            Schema: <code className="font-mono">{`{text, icp_segment, persona, theme, sentiment, source}`}</code>.
            Vector index: <code className="font-mono">customer_voice_vector</code> (Voyage AI 1024-d embeddings).
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
