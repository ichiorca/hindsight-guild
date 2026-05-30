/**
 * Agent identity — the team in the UI.
 *
 * Every place an agent shows up (queue badges, drafting stepper, weekly
 * review activity, live ops heartbeats), we pull from this single registry
 * so they read as a team with personalities, not as function names.
 */

import type { LucideIcon } from "lucide-react";
import {
  Search,
  PencilLine,
  CheckSquare,
  BarChart3,
  Compass,
  Target,
  Quote,
  Mail,
  DollarSign,
  ShieldCheck,
  Brain,
  Image as ImageIcon,
} from "lucide-react";

export type AgentId =
  | "research_agent"
  | "content_agent"
  | "review_agent"
  | "analytics_agent"
  | "cmo_planner"
  | "positioning_agent"
  | "customer_voice_agent"
  | "lifecycle_email_agent"
  | "paid_media_agent"
  | "ops_qa_agent"
  | "self_critique_agent"
  | "image_brief_agent";

export interface AgentProfile {
  id: AgentId;
  name: string;
  shortName: string;
  role: string;
  icon: LucideIcon;
  // Tailwind classes built from the hsl(var(--agent-*)) tokens
  colorClass: string;       // background tint
  textClass: string;        // text color on the tint
  ringClass: string;        // ring color (subtle outline)
  initials: string;
}

export const AGENTS: Record<AgentId, AgentProfile> = {
  research_agent: {
    id: "research_agent",
    name: "Research",
    shortName: "Research",
    role: "Listens to the market",
    icon: Search,
    colorClass: "bg-agent-research/10",
    textClass: "text-agent-research",
    ringClass: "ring-agent-research/30",
    initials: "RS",
  },
  content_agent: {
    id: "content_agent",
    name: "Content",
    shortName: "Content",
    role: "Drafts the work",
    icon: PencilLine,
    colorClass: "bg-agent-content/10",
    textClass: "text-agent-content",
    ringClass: "ring-agent-content/30",
    initials: "CT",
  },
  review_agent: {
    id: "review_agent",
    name: "Review",
    shortName: "Review",
    role: "Guards the brand",
    icon: CheckSquare,
    colorClass: "bg-agent-review/10",
    textClass: "text-agent-review",
    ringClass: "ring-agent-review/30",
    initials: "RV",
  },
  analytics_agent: {
    id: "analytics_agent",
    name: "Analytics",
    shortName: "Analytics",
    role: "Reads the numbers",
    icon: BarChart3,
    colorClass: "bg-agent-analytics/10",
    textClass: "text-agent-analytics",
    ringClass: "ring-agent-analytics/30",
    initials: "AN",
  },
  cmo_planner: {
    id: "cmo_planner",
    name: "CMO",
    shortName: "CMO",
    role: "Plans the week",
    icon: Compass,
    colorClass: "bg-agent-cmo/10",
    textClass: "text-agent-cmo",
    ringClass: "ring-agent-cmo/30",
    initials: "CM",
  },
  positioning_agent: {
    id: "positioning_agent",
    name: "Positioning",
    shortName: "Positioning",
    role: "Tends the messaging library",
    icon: Target,
    colorClass: "bg-agent-positioning/10",
    textClass: "text-agent-positioning",
    ringClass: "ring-agent-positioning/30",
    initials: "PO",
  },
  customer_voice_agent: {
    id: "customer_voice_agent",
    name: "Customer Voice",
    shortName: "Voice",
    role: "Hears the customer",
    icon: Quote,
    colorClass: "bg-agent-customer-voice/10",
    textClass: "text-agent-customer-voice",
    ringClass: "ring-agent-customer-voice/30",
    initials: "CV",
  },
  lifecycle_email_agent: {
    id: "lifecycle_email_agent",
    name: "Lifecycle Email",
    shortName: "Lifecycle",
    role: "Nurtures the funnel",
    icon: Mail,
    colorClass: "bg-agent-lifecycle-email/10",
    textClass: "text-agent-lifecycle-email",
    ringClass: "ring-agent-lifecycle-email/30",
    initials: "LE",
  },
  paid_media_agent: {
    id: "paid_media_agent",
    name: "Paid Media",
    shortName: "Paid",
    role: "Watches the spend",
    icon: DollarSign,
    colorClass: "bg-agent-paid-media/10",
    textClass: "text-agent-paid-media",
    ringClass: "ring-agent-paid-media/30",
    initials: "PM",
  },
  ops_qa_agent: {
    id: "ops_qa_agent",
    name: "Ops/QA",
    shortName: "Ops",
    role: "Keeps the plumbing honest",
    icon: ShieldCheck,
    colorClass: "bg-agent-ops-qa/10",
    textClass: "text-agent-ops-qa",
    ringClass: "ring-agent-ops-qa/30",
    initials: "OP",
  },
  self_critique_agent: {
    id: "self_critique_agent",
    name: "Self-Critique",
    shortName: "Critique",
    role: "Reviews the team",
    icon: Brain,
    colorClass: "bg-agent-self-critique/10",
    textClass: "text-agent-self-critique",
    ringClass: "ring-agent-self-critique/30",
    initials: "SC",
  },
  image_brief_agent: {
    id: "image_brief_agent",
    name: "ImageBrief",
    shortName: "Image",
    role: "Pictures the work",
    icon: ImageIcon,
    colorClass: "bg-agent-image-brief/10",
    textClass: "text-agent-image-brief",
    ringClass: "ring-agent-image-brief/30",
    initials: "IB",
  },
};

export function getAgent(id: string | undefined | null): AgentProfile | null {
  if (!id) return null;
  return AGENTS[id as AgentId] ?? null;
}

/** The 5 "front line" agents shown in the sidebar avatar stack (others
 *  appear on demand in Live Ops and contextual UI). */
export const FRONT_LINE_AGENT_IDS: AgentId[] = [
  "research_agent",
  "content_agent",
  "review_agent",
  "analytics_agent",
  "cmo_planner",
];
