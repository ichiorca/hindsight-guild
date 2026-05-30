/**
 * Plain-language converters for backend identifiers and field values.
 *
 * The UI surfaces a lot of agent telemetry — action_types like
 * ``draft_blog`` and ``paid_media_revise_op``, skill_ids like
 * ``linkedin_post_v3.txt``, status values like ``awaiting_human_review``
 * — that come straight from the data model. Showing them verbatim
 * makes the product feel like a developer dashboard. These helpers
 * convert each to a sentence a marketer would actually write.
 *
 * Use these everywhere a raw identifier would otherwise be displayed.
 */
import { getAgent } from "./agents";

const ACTION_TYPE_VERBS: Record<string, string> = {
  // Drafting pipeline
  draft_op:                   "Drafted a post",
  draft_linkedin:             "Drafted a LinkedIn post",
  draft_email:                "Drafted an email",
  draft_blog:                 "Drafted a blog",
  draft_substack:             "Drafted a Substack newsletter",
  draft_lifecycle_email:      "Drafted an email sequence step",
  research_op:                "Researched the audience",
  review_op:                  "Reviewed the draft",
  critique_op:                "Critiqued the draft",
  revise_op:                  "Revised the draft",
  image_brief_op:             "Created the hero image",
  finalizer_op:               "Finalized the asset",
  // Specialist agents
  paid_media_op:              "Proposed paid variants",
  paid_media_critique_op:     "Critiqued paid variants",
  paid_media_revise_op:       "Revised paid variants",
  lifecycle_email_op:         "Drafted an email sequence",
  lifecycle_email_critique_op: "Critiqued an email sequence",
  lifecycle_email_revise_op:  "Revised an email sequence",
  draft_email_sequence:       "Drafted an email sequence",
  positioning_op:             "Proposed positioning",
  customer_voice_ingest:      "Ingested customer voice",
  voice_ingest_op:            "Ingested customer voice",
  cmo_plan_op:                "Composed the weekly memo",
  analytics_op:               "Pulled this week's analytics",
  ops_qa_op:                  "Ran the ops sweep",
  self_critique_op:           "Reviewed the team's playbooks",
};

/**
 * Map a raw action_type to a verb phrase a marketer would write.
 * Falls back to title-cased version when unknown.
 */
export function humanizeActionType(actionType: string | null | undefined): string {
  if (!actionType) return "Activity";
  const verb = ACTION_TYPE_VERBS[actionType];
  if (verb) return verb;
  // Generic fallback: "paid_media_drafter" → "Paid media drafter"
  return actionType
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());
}

/**
 * Map a skill_id or version string to a human label. Strips file
 * extensions and trailing version tokens that aren't meaningful to a
 * non-engineer. ``linkedin_post_v3.txt`` → "LinkedIn post (v3)".
 */
export function humanizeSkillName(skillId: string | null | undefined): string {
  if (!skillId) return "—";
  // Strip file extension
  let s = skillId.replace(/\.(txt|md)$/i, "");
  // Pull off trailing version: foo_v3 → name=foo, version=v3
  const m = s.match(/^(.+)_v(\d+)$/i);
  let version = "";
  if (m) {
    s = m[1];
    version = ` (v${m[2]})`;
  }
  const name = s
    .replace(/[-_]/g, " ")
    // Title-case each word, preserve common acronyms
    .split(" ")
    .map((w) =>
      /^(cro|cmo|ab|qa|seo|aeo|nps|rsa|ctr|mde|icp|csm|ae)$/i.test(w)
        ? w.toUpperCase()
        : w.charAt(0).toUpperCase() + w.slice(1),
    )
    .join(" ");
  return name + version;
}

const STATUS_LABELS: Record<string, string> = {
  proposed:                "Proposed",
  approved:                "Approved",
  rejected:                "Rejected",
  paused:                  "Paused",
  draft:                   "Draft",
  running:                 "Running",
  decided:                 "Decided",
  archived:                "Archived",
  awaiting_human_review:   "Awaiting your review",
  awaiting_approval:       "Awaiting approval",
  publishing:              "Publishing…",
  published:               "Published",
  failed:                  "Failed",
  manual_review:           "Needs manual review",
  open:                    "Open",
  closed:                  "Closed",
  pending:                 "Pending",
  done:                    "Done",
};

export function humanizeStatus(status: string | null | undefined): string {
  if (!status) return "—";
  return STATUS_LABELS[status] ?? status.replace(/_/g, " ");
}

const REVIEW_ISSUE_LABELS: Record<string, string> = {
  needs_evidence:        "Claim needs evidence",
  overclaim:             "Overclaims",
  off_brand:             "Off-brand tone",
  off_icp:               "Wrong audience fit",
  originality:           "Sounds derivative",
  image_missing:         "Image needs manual upload",
  image_overclaim:       "Image overclaims",
  image_likeness_risk:   "Image likeness risk",
  claim_risk:            "Claim risk",
  tone:                  "Tone",
  conversion_intent:     "Hard-sell / multi-CTA",
};

export function humanizeReviewIssue(issue: string | null | undefined): string {
  if (!issue) return "—";
  return REVIEW_ISSUE_LABELS[issue] ?? issue.replace(/_/g, " ");
}

/**
 * Friendly display name for an agent. Falls back to the registry's
 * shortName, then to a title-cased version of the id.
 */
export function humanizeAgentName(agentId: string | null | undefined): string {
  if (!agentId) return "Unknown agent";
  const profile = getAgent(agentId);
  if (profile) return profile.name;
  // Sub-agent rollup: lifecycle_email_drafter → "Lifecycle Email"
  if (agentId.startsWith("lifecycle_email_")) return "Lifecycle Email";
  if (agentId.startsWith("paid_media_")) return "Paid Media";
  return agentId
    .replace(/_agent$/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const TOOL_LABELS: Record<string, string> = {
  "mongodb (read)":           "Read team knowledge",
  "mongodb (write)":          "Write team knowledge",
  "mongodb (read+write via reviser)": "Manages variant inventory",
  "bigquery_query":           "Analytics database",
  "validate_claim":           "Fact-check claims",
  "web_search":               "Web search",
  "search_past_lessons":      "Past lessons memory",
  "skill_tools":              "Playbook library",
  "check_image_safety":       "Image safety check",
  "imagen_generate":          "AI image generation",
  "AgentTool(research)":      "Calls Research agent",
  "AgentTool(analytics)":     "Calls Analytics agent",
  "slack_approval":           "Posts to Slack for approval",
  "http_health_check":        "URL uptime monitoring",
};

export function humanizeToolName(tool: string): string {
  return TOOL_LABELS[tool] ?? tool.replace(/_/g, " ");
}

const SCHEDULED_JOB_LABELS: Record<string, string> = {
  "outcome-attach":   "Outcome attribution",
  "eval-harness":     "Quality re-grading",
  "drift-detect":     "Drift detection",
  "self-critique":    "Self-critique sweep",
  "promotion-gate":   "Promotion review",
};

export function humanizeScheduledJob(name: string): string {
  return SCHEDULED_JOB_LABELS[name] ?? name.replace(/-/g, " ");
}

/**
 * Compact "X minutes ago" style for activity feeds.
 */
export function relativeTime(iso: string | Date | null | undefined): string {
  if (!iso) return "—";
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  return `${Math.floor(diff / 86400)} days ago`;
}
