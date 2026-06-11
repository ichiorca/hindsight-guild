import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(d: string | Date | undefined | null): string {
  if (!d) return "—";
  const dt = typeof d === "string" ? new Date(d) : d;
  if (isNaN(dt.getTime())) return "—";
  return dt.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgo(d: string | Date | undefined | null): string {
  if (!d) return "—";
  const dt = typeof d === "string" ? new Date(d) : d;
  const diff = (Date.now() - dt.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function pct(n: number | undefined | null, decimals = 0): string {
  if (n == null || isNaN(n)) return "—";
  return `${(n * 100).toFixed(decimals)}%`;
}

export function scoreColor(s: number | undefined | null): string {
  if (s == null) return "text-muted-foreground";
  if (s >= 0.8) return "text-green-600 dark:text-green-400";
  if (s >= 0.65) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

// Human labels for the ICP segment ids that the seed + pipeline use.
// Keep in sync with mongo/data/customer_voice.py + scripts/local_seed.py.
export const ICP_LABELS: Record<string, string> = {
  // Agentic-commerce ICPs — merchants are the primary buyer.
  seg_merchant_dtc: "Merchant / DTC brand",
  seg_ecom_leader: "E-commerce leader",
  seg_payments_network: "Payments / network",
  seg_agent_platform: "Agent platform",
};

export function icpLabel(slug: string | null | undefined): string {
  if (!slug) return "—";
  return ICP_LABELS[slug] ?? slug.replace(/^seg_/, "").replace(/_/g, " ");
}

// Channel display + ordering for dropdowns / chips. Grouped logically:
//   - Organic / owned (linkedin, blog, substack, email)
//   - Lifecycle (nurture sequences — multi-step, routed to lifecycle_email_agent)
//   - Paid (per-platform; routed to paid_media_agent with platform-specific
//     format caps enforced in the critique step)
//
// Keep these IDs in sync with:
//   - agents/pipeline.py `_CHANNEL_RE` + `_SKILL_BY_CHANNEL`
//   - shared/imagen.py `CHANNEL_ASPECT`
//   - services/web_api channel routing
export const CHANNELS: Array<{
  id: string;
  label: string;
  description: string;
  group?: "organic" | "lifecycle" | "paid";
}> = [
  { id: "linkedin",      label: "LinkedIn post → LinkedIn",       description: "Feed post — aim under 1,300 chars (the see-more fold); hard platform limit 3,000. Publishes to LinkedIn on approval.", group: "organic" },
  { id: "linkedin_article", label: "LinkedIn article",            description: "Long-form article (800–1,500 words, # Title first). LinkedIn's API can't create articles — approved drafts are pasted into the LinkedIn editor manually.", group: "organic" },
  { id: "blog",          label: "Blog outline → Dev.to",          description: "Structured long-form post. Publishes to Dev.to on approval.",                       group: "organic" },
  { id: "substack",      label: "Substack newsletter → Substack", description: "Headline + subtitle + markdown body. Publishes to Substack on approval.",            group: "organic" },
  { id: "email",         label: "One-off email",                  description: "Single mid-funnel campaign send (drafted, not auto-published)",                      group: "organic" },
  { id: "lifecycle_email", label: "Nurture sequence",  description: "3–5 step lifecycle email sequence (drafted, never auto-sent)", group: "lifecycle" },
  { id: "google_ads",    label: "Google Ads (RSA)",    description: "3x30-char headlines + 90-char descriptions, paused on insert", group: "paid" },
  { id: "meta_ads",      label: "Meta Ads",            description: "40-char headline + 125-char primary text, paused on insert",   group: "paid" },
  { id: "linkedin_ads",  label: "LinkedIn Ads",        description: "70-char headline + 150-char body, paused on insert",           group: "paid" },
];

export function channelLabel(id: string | null | undefined): string {
  if (!id) return "—";
  return CHANNELS.find((c) => c.id === id)?.label ?? id;
}
