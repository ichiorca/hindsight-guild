/**
 * Sidebar + mobile nav — single source of truth.
 *
 * Organized into three sections that match how the founder reasons
 * about the product:
 *
 *   - Decisions: the daily transactional inbox. Anything with a queue
 *     of items that need a yes/no/edit.
 *   - Skill library: institutional knowledge — what the system knows
 *     and remembers. Read-mostly reference.
 *   - Signals & Trends: observability. What has happened, what is
 *     happening right now, what's trending.
 *
 * Layout.tsx + Topbar.tsx + MobileNav.tsx all read from this file so
 * the desktop sidebar and mobile drawer never drift out of sync.
 */
import {
  Inbox, CalendarCheck, PencilLine, FlaskConical, Layers, Quote, LineChart,
  Activity, BookOpen, Users, Radar, Brain, Send, type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  description: string;
  /** Optional live-count badge driver. Layout.tsx fans out to the right
   * hook per source so the nav stays declarative. The "pending_proposals"
   * source maps to /api/self-critique/summary.pending_proposals — used
   * by Weekly Review to alert the founder there's work waiting. */
  badge?: "pending_proposals";
}

export interface NavSection {
  title: string;       // section header shown above its items
  hint?: string;       // optional one-liner under the header
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Decisions",
    hint: "What needs your call today",
    items: [
      // Daily-driver first: the Queue is the founder's #1 landing page.
      { to: "/queue",         label: "Approval Queue", icon: Inbox,         description: "Decide on drafts" },
      // Drafting is the create action — second so it's one click away.
      { to: "/draft",         label: "Drafting",       icon: PencilLine,    description: "Run the pipeline" },
      // Signals — auto-triggers that land in the queue with a Triggered-by chip.
      { to: "/signals",       label: "Signals",        icon: Radar,         description: "Inbound triggers" },
      // Published — the durable record of what actually shipped externally.
      { to: "/published",     label: "Published",      icon: Send,          description: "What shipped + where" },
      // Monday ritual — the slowest cadence, so last. Carries the
      // pending-proposals badge so the founder sees decision work
      // waiting without opening the page.
      { to: "/weekly-review", label: "Weekly Review",  icon: CalendarCheck, description: "Your Monday ritual",
        badge: "pending_proposals" },
    ],
  },
  {
    title: "Skill library",
    hint: "What the system knows",
    items: [
      // Playbooks — the most-referenced library page.
      { to: "/skills",        label: "Skills",         icon: Layers,        description: "Playbook versions" },
      // Capabilities — per-agent skill access + the usage heatmap.
      { to: "/capabilities",  label: "Capabilities",   icon: BookOpen,      description: "Agent skills + usage" },
      // Customer voice — the grounding source for every draft.
      { to: "/voice",         label: "Customer Voice", icon: Quote,         description: "What customers say" },
      // Agent roster + per-agent inbox.
      { to: "/agents",        label: "Agents",         icon: Users,         description: "Team roster + inboxes" },
    ],
  },
  {
    title: "Signals & Trends",
    hint: "What's happened, what's happening",
    items: [
      // Self-Learning — the closed-loop story. Top of the section so
      // founders + future hires can see at a glance that the system
      // is actually getting better.
      { to: "/learning",      label: "Self-Learning",  icon: Brain,         description: "How the team improves" },
      // Renamed to match the page title — was "Telemetry" in the nav,
      // which conflicted with the in-page heading "Quality Signals".
      { to: "/telemetry",     label: "Quality Signals", icon: LineChart,    description: "Rubric + drift + AI citations" },
      // Experiments registry — running / decided / drift investigations.
      { to: "/experiments",   label: "Experiments",    icon: FlaskConical,  description: "Hypotheses in flight" },
      // Live ops feed — the right-now ticker for ops + WS-pushed events.
      { to: "/live",          label: "Live Ops",       icon: Activity,      description: "What's running now" },
    ],
  },
];

/** Flat list view used by places that don't render section headers
 * (e.g., the CommandPalette's "all routes" surface). */
export const NAV_FLAT: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);
