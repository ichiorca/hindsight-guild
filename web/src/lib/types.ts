export type PublishState = "publishing" | "published" | "manual_review" | "failed";

export interface DraftImage {
  url: string | null;
  alt_text: string;
  aspect_ratio: string;
  mode: "api" | "stub";
  prompt?: string;
  rationale?: string;
  kind?: string;   // contextual | infographic | excalidraw
}

export interface QueueItem {
  telemetry_id: string;
  ts: string;
  agent: string;
  channel: string | null;
  skill_id: string;
  skill_version: string;
  subject?: string | null;
  draft_text: string;
  eval_scores: EvalScores;
  review_flags: ReviewFlag[];
  customer_voice_used: string[];
  icp_segment: string | null;
  experiment_id: string | null;
  image?: DraftImage | null;
  images?: DraftImage[] | null;   // ImageBrief's 1-3 visuals
  publish_state?: PublishState | null;
  publish_url?: string | null;
  publish_mode?: "api" | "manual_review" | null;
  // PRD-02 — populated on signal-triggered drafts only.
  triggered_by_signal_id?: string | null;
  triggered_by_source?: string | null;
  triggered_by_evidence_url?: string | null;
  triggered_by_ts?: string | null;
}

export interface EvalScores {
  brand_voice?: number;
  claim_support?: number;
  claim_risk?: number;
  icp_relevance?: number;
  originality?: number;
  conversion_intent?: number;
  // PRD-01: 7th rubric — populated by the AEO loop on blog/substack/linkedin
  // drafts only. ``null`` (or absent) when the channel has no answer-engine
  // surface (paid + email) OR when the draft was too short to score.
  answer_extractability?: number;
}

export interface ReviewFlag {
  phrase: string;
  issue: string;
}

export type Decision = "approve" | "edit" | "reject";

export interface DecisionPayload {
  telemetry_id: string;
  decision: Decision;
  original_draft: string;
  approved_text?: string;
  rejection_reason?: string;
  channel?: string | null;
  decided_by?: string;
}

export interface Experiment {
  _id: string;
  title: string;
  hypothesis: string;
  channel?: string;
  icp_segment?: string;
  state: "proposed" | "running" | "decided" | "archived";
  variants: Array<{
    id: string;
    playbook_version: string;
    allocation_pct: number;
  }>;
  success_metric: string;
  mde?: number;
  decision_rule?: string;
  decided_at?: string;
  result?: {
    winner?: string;
    lift?: number;
    p_value?: number;
  };
  lesson?: string;
  tags?: string[];
}

export interface Skill {
  _id: string;
  current_version: string;
  candidates: string[];
  history: string[];
  applies_to: {
    icp_segments: string[];
    channels: string[];
  };
  promoted_at?: string;
  promoted_from_experiment?: string;
  track_record?: Record<string, {
    action_count: number;
    mean_brand_voice: number;
    mean_claim_support: number;
  }>;
  self_critique_proposal?: {
    candidate_id?: string;               // legacy LlmAgent envelope only
    issue: string;
    proposed_change: string;
    confidence: "high" | "medium" | "low";
    evidence_count: number;
    status: string;
    // PRD-03 miner envelope adds these. ``miner`` identifies which
    // nightly miner emitted the proposal; ``evidence`` is the
    // miner-specific dict EvidenceDrawer renders.
    miner?: string;
    kind?: string;
    evidence?: Record<string, unknown>;
    proposed_at?: string;
    run_id?: string;
  };
  promotion_request?: {
    candidate: string;
    incumbent: string;
    // "agent_skill" for Skill-library promotions (proposed_diff path),
    // undefined/missing for legacy playbook A/B promotions (lift path).
    kind?: "agent_skill" | "playbook";
    status: string;

    // ----- Playbook (A/B-lift) fields — present when kind != "agent_skill"
    success_metric?: string;
    lift?: number;
    candidate_stats?: Record<string, number>;
    candidate_n?: number;
    incumbent_stats?: Record<string, number>;
    incumbent_n?: number;

    // ----- Agent-Skill fields — present when kind === "agent_skill"
    issue?: string;
    proposed_diff?: string;            // unified-diff-ish markdown patch
    channels_at_risk?: string[];
    per_channel_baseline?: Record<string, Record<string, number>>;
    project_baseline_brand_voice?: number;
    evidence_count?: number;
    confidence?: "high" | "medium" | "low";
  };
}

export interface VoiceQuote {
  _id: string;
  text: string;
  icp_segment: string;
  persona?: string;
  theme: string;
  source: string;
  sentiment: "positive" | "neutral" | "negative";
}

export interface RubricTrendPoint {
  day: string;
  channel: string;
  mean_brand_voice: number;
  mean_claim_support: number;
  n: number;
}

export interface WeekSummary {
  drafts: number;
  edits: number;
  approvals: number;
  armor_blocks: number;
  total_actions: number;
}

export interface WeeklyReview {
  summary: WeekSummary;
  edit_categories: Array<{ category: string; n: number }>;
  decided_experiments: Experiment[];
  running_experiments: Experiment[];
  drift_investigations: Experiment[];
  self_critique_proposals: Skill[];
  promotion_requests: Skill[];
  rubric_trend: RubricTrendPoint[];
}

// ----- Capabilities (Agent Skills + usage telemetry) -----

export interface SkillCatalogEntry {
  name: string;
  description: string;
  version: string;
  reference_count: number;
  allowed_for: string[];
}

export interface SkillUsage {
  skill_name: string;
  loads: number;
  loads_prev: number;
  tokens_estimated: number;
  last_used_at: string;
  top_agents: Array<{ agent_name: string; count: number }>;
}

export interface AgentSkillUsage {
  agent_name: string;
  total_loads: number;
  skill_loads: Array<{ skill_name: string; count: number }>;
}

export interface SkillTimeseriesPoint {
  day: string;
  tier_2: number;
  tier_3: number;
}

export interface RecentSkillLoad {
  ts: string;
  agent_name?: string;
  skill_name: string;
  tier: number;
  reference_path?: string | null;
  tokens_estimated?: number;
}

export interface CapabilitiesSummary {
  installed_skills: number;
  loads_period: number;
  loads_prev_period: number;
  loads_delta_pct: number | null;
  tokens_estimated: number;
  unique_skills_used: number;
  dead_weight_count: number;
  dead_weight: string[];
}

export interface CapabilitiesData {
  window_days: number;
  catalog: SkillCatalogEntry[];
  summary: CapabilitiesSummary;
  by_skill: SkillUsage[];
  by_agent: AgentSkillUsage[];
  timeseries: SkillTimeseriesPoint[];
  recent: RecentSkillLoad[];
}
