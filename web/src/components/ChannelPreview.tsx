/**
 * Channel-native preview — a LinkedIn post should look like a LinkedIn post.
 *
 * Each channel renders the draft text in its native chrome so the founder
 * decides on what the world will actually see, not on monospace card text.
 */

import { Mail as MailIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * If the ENTIRE text is one fenced code block (```/```json), unwrap it —
 * models sometimes fence a whole structured draft, which broke JSON parsing
 * and made cards show a literal "```json" headline. Mid-text fences (real
 * code samples) are untouched. Mirrors drafting.py::unfence.
 */
function unfence(text: string): string {
  const m = (text || "").match(/^\s*```[a-zA-Z0-9_-]*[ \t]*\r?\n([\s\S]*?)\r?\n?```\s*$/);
  return m ? m[1].trim() : text;
}

/**
 * LinkedIn renders plain text only — mirror shared/integrations/linkedin.py's
 * _strip_markdown so the preview shows exactly what ships (drafts sometimes
 * arrive with markdown bold/bullets; the publish adapter strips them).
 */
function stripMarkdown(body: string): string {
  let s = body || "";
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)");
  s = s.replace(/^#{1,6}\s+/gm, "");
  s = s.replace(/\*\*(.+?)\*\*/g, "$1");
  s = s.replace(/(^|\s)\*(?!\s)(.+?)(?<!\s)\*(?=\s|$)/g, "$1$2");
  s = s.replace(/^\s*\*\s+/gm, "• ");
  s = s.replace(/__(.+?)__/g, "$1");
  s = s.replace(/`+/g, "");
  s = s.replace(/^>\s?/gm, "");
  return s.trim();
}

/** Render `**bold**` spans inline; everything else verbatim. */
function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  if (parts.length === 1) return text;
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**")
      ? <strong key={i}>{p.slice(2, -2)}</strong>
      : p,
  );
}

/** Bullet line in either markdown flavor (`- ` or `* `). */
function bulletText(trimmed: string): string | null {
  if (trimmed.startsWith("- ")) return trimmed.slice(2);
  if (/^\*\s+/.test(trimmed)) return trimmed.replace(/^\*\s+/, "");
  return null;
}

export interface PreviewImage {
  url: string | null;
  alt_text: string;
  aspect_ratio?: string;
  mode?: "api" | "stub";
  kind?: string;   // contextual | infographic | excalidraw
}

interface ChannelPreviewProps {
  channel: string | null;
  text: string;
  subject?: string;
  image?: PreviewImage | null;
  images?: PreviewImage[] | null;   // ImageBrief's 1-3 visuals
  className?: string;
}

// Render the secondary visuals (beyond the in-context primary) as a labeled grid.
function ExtraVisuals({ images }: { images: PreviewImage[] }) {
  return (
    <div className="space-y-2">
      <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">
        + {images.length} more visual{images.length > 1 ? "s" : ""}
      </p>
      <div className="grid grid-cols-2 gap-2">
        {images.map((im, i) => (
          <div key={i} className="space-y-1">
            <ImageBlock image={im} aspect="16/9" />
            {im.kind && (
              <span className="text-[10px] text-muted-foreground capitalize">{im.kind}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ChannelPreview({ channel, text, subject, image, images, className }: ChannelPreviewProps) {
  text = unfence(text);
  const imgs = images && images.length ? images : image ? [image] : [];
  const primary = imgs[0] ?? null;
  const inner =
    channel === "linkedin" ? <LinkedInPreview text={text} image={primary} className={className} />
    : channel === "linkedin_article" ? <LinkedInArticlePreview text={text} image={primary} className={className} />
    : channel === "email" ? <EmailPreview text={text} subject={subject} image={primary} className={className} />
    : channel === "blog" ? <BlogPreview text={text} image={primary} className={className} />
    : channel === "substack" ? <SubstackPreview text={text} image={primary} className={className} />
    : <DefaultPreview text={text} image={primary} className={className} />;
  if (imgs.length <= 1) return inner;
  return (
    <div className="space-y-3">
      {inner}
      <ExtraVisuals images={imgs.slice(1)} />
    </div>
  );
}

/**
 * Channel-agnostic image renderer. Handles the api/stub modes uniformly:
 * - api  → render the image
 * - stub → render a styled placeholder card explaining "image pending"
 */
function ImageBlock({
  image, aspect = "16/9", rounded = true, className,
}: {
  image?: PreviewImage | null;
  aspect?: string;
  rounded?: boolean;
  className?: string;
}) {
  if (!image) return null;
  if (image.url) {
    // Diagrams (infographic/excalidraw) must show in FULL — object-cover would
    // crop the labels. Photos/contextual can fill+crop. Diagrams get a white
    // backdrop so the letterboxing reads clean.
    const isDiagram = image.kind === "infographic" || image.kind === "excalidraw";
    return (
      <img
        src={image.url}
        alt={image.alt_text}
        className={cn(
          "w-full",
          isDiagram ? "object-contain bg-white" : "object-cover",
          rounded && "rounded-md",
          className,
        )}
        style={{ aspectRatio: aspect }}
        loading="lazy"
      />
    );
  }
  // stub
  return (
    <div
      className={cn(
        "w-full flex flex-col items-center justify-center text-center px-4",
        "border-2 border-dashed border-warning/40 bg-warning/5 text-muted-foreground",
        rounded && "rounded-md",
        className,
      )}
      style={{ aspectRatio: aspect }}
    >
      <span className="text-[11px] uppercase tracking-wider text-warning font-semibold mb-1">
        {image.kind === "infographic" || image.kind === "excalidraw" ? "Diagram pending" : "Image pending"}
      </span>
      <p className="text-xs italic max-w-xs">"{image.alt_text}"</p>
      <p className="text-[10px] text-muted-foreground mt-1.5">
        {image.kind === "infographic" || image.kind === "excalidraw"
          ? "Diagram render failed · founder uploads on publish"
          : "Image generation failed · founder uploads on publish"}
      </p>
    </div>
  );
}

// LinkedIn POST limits: ~1,300 chars is the feed "see more" fold guidance;
// 3,000 is the hard platform limit a publish cannot exceed.
const LINKEDIN_POST_FOLD = 1300;
const LINKEDIN_POST_MAX = 3000;

function LinkedInPostLengthBar({ chars }: { chars: number }) {
  const over = chars > LINKEDIN_POST_MAX;
  const longish = !over && chars > LINKEDIN_POST_FOLD;
  return (
    <div className={cn(
      "px-4 py-2 border-t border-[#e6e9ec] dark:border-[#38434f] text-[11px] tabular-nums",
      over ? "text-destructive font-medium" : "text-[#666] dark:text-[#a0a8af]",
    )}>
      {chars.toLocaleString()} / {LINKEDIN_POST_MAX.toLocaleString()} chars
      {over && " — exceeds LinkedIn's post limit; trim it, or draft as a LinkedIn article"}
      {longish && ` — above the ~${LINKEDIN_POST_FOLD.toLocaleString()}-char "see more" fold`}
    </div>
  );
}

function LinkedInPreview({ text, image, className }: { text: string; image?: PreviewImage | null; className?: string }) {
  text = stripMarkdown(text);
  return (
    <div className={cn(
      "rounded-lg border bg-white text-[#1d2226] shadow-sm overflow-hidden",
      "dark:bg-[#1b1f23] dark:text-[#f3f6f8] dark:border-[#38434f]",
      className,
    )}>
      <div className="p-4 border-b border-[#e6e9ec] dark:border-[#38434f]">
        <div className="flex items-start gap-3">
          <div className="h-12 w-12 rounded-full bg-gradient-to-br from-[#0a66c2] to-[#004182] flex items-center justify-center text-white font-semibold text-sm shrink-0">
            YOU
          </div>
          <div className="flex flex-col min-w-0">
            <span className="font-semibold text-[14px] leading-tight">Founder</span>
            <span className="text-[12px] text-[#666] dark:text-[#a0a8af]">Solo founder · building in public · 1st</span>
            <span className="text-[12px] text-[#666] dark:text-[#a0a8af]">now · 🌎</span>
          </div>
        </div>
        <div className="mt-3 text-[14px] leading-[1.45] whitespace-pre-wrap font-sans">
          {text || <span className="text-[#666] italic">draft is empty</span>}
        </div>
      </div>
      {image && (
        <div>
          <ImageBlock image={image} aspect="1/1" rounded={false} />
        </div>
      )}
      <LinkedInPostLengthBar chars={text.length} />
    </div>
  );
}

/**
 * LinkedIn ARTICLE — long-form, distinct from a feed post (no char cap, but
 * LinkedIn's API can't create articles: approval saves the draft for manual
 * paste into the LinkedIn editor). Renders title + prose like the editor.
 */
function LinkedInArticlePreview({ text, image, className }: { text: string; image?: PreviewImage | null; className?: string }) {
  const { title, body } = parseBlogDraft(text);
  const words = body.trim().split(/\s+/).length;
  return (
    <div className={cn(
      "rounded-lg border bg-white text-[#1d2226] shadow-sm overflow-hidden",
      "dark:bg-[#1b1f23] dark:text-[#f3f6f8] dark:border-[#38434f]",
      className,
    )}>
      <div className="px-6 pt-4 text-[11px] uppercase tracking-wider text-[#666] dark:text-[#a0a8af] font-medium">
        LinkedIn article · {words.toLocaleString()} words · published manually via LinkedIn's editor
      </div>
      {image && (
        <div className="mt-3 border-y border-[#e6e9ec] dark:border-[#38434f]">
          <ImageBlock image={image} aspect="16/9" rounded={false} />
        </div>
      )}
      <div className="px-6 py-5">
        <h1 className="text-[26px] font-bold leading-tight tracking-tight">{title}</h1>
        <div className="flex items-center gap-2 mt-3 mb-5 pb-4 border-b border-[#e6e9ec] dark:border-[#38434f] text-[12px] text-[#666] dark:text-[#a0a8af]">
          <div className="h-8 w-8 rounded-full bg-gradient-to-br from-[#0a66c2] to-[#004182] flex items-center justify-center text-white font-semibold text-[11px]">
            YOU
          </div>
          <span className="font-semibold text-[#1d2226] dark:text-[#f3f6f8]">Founder</span>
          <span>·</span>
          <span>Solo founder · building in public</span>
        </div>
        <div className="text-[15px] leading-[1.6]">
          {body.split("\n").map((line, i) => {
            const trimmed = line.trim();
            if (!trimmed) return <div key={i} className="h-3" />;
            if (trimmed.startsWith("## "))
              return <h2 key={i} className="text-[20px] font-semibold mt-6 mb-2">{renderInline(trimmed.slice(3))}</h2>;
            if (trimmed.startsWith("# "))
              return <h2 key={i} className="text-[22px] font-semibold mt-6 mb-2">{renderInline(trimmed.slice(2))}</h2>;
            const bullet = bulletText(trimmed);
            if (bullet !== null)
              return <li key={i} className="ml-6 list-disc my-1">{renderInline(bullet)}</li>;
            return <p key={i} className="mb-3">{renderInline(trimmed)}</p>;
          })}
        </div>
      </div>
    </div>
  );
}

function EmailPreview({ text, subject, image, className }: { text: string; subject?: string; image?: PreviewImage | null; className?: string }) {
  // Parse subject from the draft if not provided (Content Agent returns
  // JSON for email; we render best-effort here)
  const parsed = parseEmailDraft(text);
  const subj = subject ?? parsed.subject ?? "(no subject)";
  const body = parsed.body ?? text;
  const subjClip = subj.length > 40 ? subj.slice(0, 40) : subj;
  const wasClipped = subj.length > 40;

  return (
    <div className={cn(
      "rounded-lg border bg-white text-foreground shadow-sm overflow-hidden",
      "dark:bg-card dark:border-border",
      className,
    )}>
      {/* Mobile preview row */}
      <div className="px-4 py-3 border-b bg-muted/30">
        <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1.5 font-medium">
          Mobile preview · what 65% of recipients see
        </div>
        <div className="flex items-start gap-3">
          <div className="h-10 w-10 rounded-full bg-gradient-to-br from-primary to-warning flex items-center justify-center text-white font-semibold text-xs shrink-0">
            YOU
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between text-[13px] font-semibold">
              <span className="truncate">Founder</span>
              <span className="text-muted-foreground text-[11px] font-normal">now</span>
            </div>
            <div className="text-[13px] font-medium text-foreground/90 mt-0.5">
              <span>{subjClip}</span>
              {wasClipped && <span className="text-destructive">…</span>}
            </div>
            <div className="text-[12px] text-muted-foreground truncate mt-0.5">
              {body.split("\n")[0]}
            </div>
          </div>
        </div>
        {wasClipped && (
          <p className="text-[11px] text-destructive mt-2 font-medium">
            Subject is {subj.length} chars — clipped at 40. Front-load the hook.
          </p>
        )}
      </div>
      {/* Full body */}
      <div className="p-5">
        <div className="text-[13px] text-muted-foreground mb-3 space-y-0.5">
          <div><span className="font-medium text-foreground">From:</span> Founder &lt;founder@yours.co&gt;</div>
          <div><span className="font-medium text-foreground">To:</span> ICP segment list (live recipient count populated at send time)</div>
          <div><span className="font-medium text-foreground">Subject:</span> {subj}</div>
        </div>
        {image && (
          <div className="-mx-5 mb-4">
            <ImageBlock image={image} aspect="16/9" rounded={false} />
          </div>
        )}
        <div className="text-[14px] leading-[1.55] whitespace-pre-wrap border-t pt-4">
          {body || <span className="text-muted-foreground italic">draft is empty</span>}
        </div>
      </div>
    </div>
  );
}

/**
 * Mirror shared/integrations/devto.py::derive_title — the title Dev.to will
 * actually get at publish: first H1 anywhere in the markdown, else the first
 * non-empty non-quote line (truncated). When an H1 is used, drop that line
 * from the displayed body so the title isn't duplicated or stranded mid-post.
 */
function parseBlogDraft(text: string): { title: string; body: string } {
  const cleaned = (text || "").replace(/```[\s\S]*?```/g, "");
  const m = cleaned.match(/^\s*#\s+(.+?)\s*$/m);
  if (m) {
    return {
      title: m[1].trim().slice(0, 120),
      body: (text || "").replace(/^\s*#\s+.+$\r?\n?/m, "").trim(),
    };
  }
  for (const line of cleaned.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith(">")) continue;
    return { title: t.length > 80 ? t.slice(0, 80) + "…" : t, body: text };
  }
  return { title: "Untitled draft", body: text };
}

function BlogPreview({ text, image, className }: { text: string; image?: PreviewImage | null; className?: string }) {
  const { title, body } = parseBlogDraft(text);
  return (
    <div className={cn(
      "rounded-lg border bg-white dark:bg-card shadow-sm overflow-hidden",
      className,
    )}>
      {image && (
        <div className="border-b">
          <ImageBlock image={image} aspect="16/9" rounded={false} />
        </div>
      )}
      <div className="px-8 pt-6">
        <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium mb-2">
          Dev.to article · title as it will publish
        </p>
        <h1 className="text-[26px] font-bold leading-tight tracking-tight">{title}</h1>
      </div>
      <div className="prose-memo px-8 py-6 max-w-none">
        {body.split("\n").map((line, i) => {
          const trimmed = line.trim();
          if (trimmed.startsWith("## ")) return <h2 key={i}>{renderInline(trimmed.slice(3))}</h2>;
          if (trimmed.startsWith("# ")) return <h2 key={i} className="text-3xl mt-2">{renderInline(trimmed.slice(2))}</h2>;
          const bullet = bulletText(trimmed);
          if (bullet !== null) return <li key={i} className="ml-5 list-disc">{renderInline(bullet)}</li>;
          if (!trimmed) return <br key={i} />;
          return <p key={i}>{renderInline(trimmed)}</p>;
        })}
      </div>
    </div>
  );
}

function SubstackPreview({ text, image, className }: { text: string; image?: PreviewImage | null; className?: string }) {
  const parsed = parseSubstackDraft(text);
  const headline = parsed.headline ?? "(no headline)";
  const subtitle = parsed.subtitle ?? "";
  const body = parsed.body ?? text;
  const wordCount = parsed.word_count ?? body.trim().split(/\s+/).length;
  const readMinutes = Math.max(1, Math.round(wordCount / 230));

  return (
    <div className={cn(
      "rounded-lg border bg-white text-[#1a1a1a] shadow-sm overflow-hidden",
      "dark:bg-card dark:text-card-foreground dark:border-border",
      className,
    )}>
      {/* Newsletter masthead — Substack-style */}
      <div className="border-b px-6 py-3 flex items-center justify-between text-[12px]">
        <div className="flex items-center gap-2 min-w-0">
          <div className="h-7 w-7 rounded-md bg-gradient-to-br from-[#ff6719] to-[#e84a05] flex items-center justify-center text-white font-serif text-[14px] font-bold shrink-0">
            S
          </div>
          <div className="flex flex-col leading-tight min-w-0">
            <span className="font-semibold truncate">Your publication</span>
            <span className="text-[10px] text-muted-foreground">Substack newsletter preview</span>
          </div>
        </div>
        <span className="px-3 py-1 rounded-full bg-[#ff6719]/10 text-[#ff6719] text-[11px] font-semibold border border-[#ff6719]/30">
          Preview
        </span>
      </div>

      {/* Mobile preview row — what email subscribers see */}
      <div className="px-6 py-3 border-b bg-muted/30">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1 font-medium flex items-center gap-1.5">
          <MailIcon className="h-3 w-3" />
          Email preview · what subscribers see in their inbox
        </div>
        <div className="text-[13px] font-semibold leading-tight">{headline}</div>
        <div className="text-[12px] text-muted-foreground truncate mt-0.5">
          {subtitle || body.split("\n")[0]?.slice(0, 100)}
        </div>
      </div>

      {/* Long-form post body */}
      <article className="px-8 py-7 max-w-none">
        <p className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
          Newsletter · {readMinutes} min read · {wordCount.toLocaleString()} words
        </p>
        <h1 className="font-serif text-[28px] font-bold tracking-tight leading-tight mt-2">
          {headline}
        </h1>
        {subtitle && (
          <p className="font-serif text-[17px] italic text-muted-foreground mt-2 leading-snug">
            {subtitle}
          </p>
        )}

        <div className="flex items-center gap-2 mt-5 mb-6 pb-4 border-b text-[12px] text-muted-foreground">
          <div className="h-7 w-7 rounded-full bg-gradient-to-br from-primary to-warning flex items-center justify-center text-white font-semibold text-[10px]">
            YOU
          </div>
          <span className="font-semibold text-foreground">Founder</span>
          <span>·</span>
          <span>now</span>
        </div>

        {/* Featured image — Substack's signature visual element */}
        {image && (
          <div className="mb-6 -mx-8">
            <ImageBlock image={image} aspect="16/9" rounded={false} />
            {image.alt_text && (
              <p className="text-[12px] italic text-muted-foreground text-center mt-2 px-8">
                {image.alt_text}
              </p>
            )}
          </div>
        )}

        <div className="font-serif text-[16px] leading-[1.7]">
          {body.split("\n").map((line, i) => {
            const trimmed = line.trim();
            if (!trimmed) return <div key={i} className="h-3" />;
            if (trimmed.startsWith("## "))
              return <h2 key={i} className="font-serif text-[22px] font-semibold mt-7 mb-3">{renderInline(trimmed.slice(3))}</h2>;
            if (trimmed.startsWith("# "))
              return <h2 key={i} className="font-serif text-[24px] font-semibold mt-7 mb-3">{renderInline(trimmed.slice(2))}</h2>;
            if (trimmed.startsWith("> "))
              return (
                <blockquote key={i} className="border-l-4 border-[#ff6719] pl-4 my-4 italic text-muted-foreground">
                  {renderInline(trimmed.slice(2))}
                </blockquote>
              );
            {
              const bullet = bulletText(trimmed);
              if (bullet !== null)
                return <li key={i} className="ml-6 list-disc my-1">{renderInline(bullet)}</li>;
            }
            return <p key={i} className="mb-4">{renderInline(trimmed)}</p>;
          })}
        </div>
      </article>
    </div>
  );
}

function DefaultPreview({ text, image, className }: { text: string; image?: PreviewImage | null; className?: string }) {
  return (
    <div className={cn("rounded-lg border bg-card overflow-hidden", className)}>
      {image && <ImageBlock image={image} aspect="16/9" rounded={false} />}
      <p className="text-sm leading-relaxed whitespace-pre-wrap p-5">{text}</p>
    </div>
  );
}

function parseSubstackDraft(text: string): {
  headline?: string; subtitle?: string; body?: string; word_count?: number;
} {
  try {
    const parsed = JSON.parse(text);
    if (parsed.headline || parsed.body_markdown) {
      return {
        headline: parsed.headline,
        subtitle: parsed.subtitle,
        body: parsed.body_markdown,
        word_count: parsed.word_count,
      };
    }
  } catch { /* not JSON, fall through */ }
  // Heuristic on markdown drafts. Drafts usually carry their title as a
  // `# Heading` line (not always first — sometimes after a preamble). Use
  // the first heading in the opening lines as the headline, WITHOUT the
  // marker, and remove that line from the body so it isn't rendered twice.
  const lines = text.split("\n");
  const headingIdx = lines.findIndex(
    (l, i) => i < 6 && /^#{1,3}\s+\S/.test(l.trim()),
  );
  if (headingIdx >= 0) {
    const headline = lines[headingIdx].trim().replace(/^#{1,3}\s+/, "");
    const body = [...lines.slice(0, headingIdx), ...lines.slice(headingIdx + 1)]
      .join("\n").trim();
    return { headline, body };
  }
  // No heading line — fall back to a short first line as the headline.
  const nonEmpty = lines.map((l) => l.trim()).filter(Boolean);
  if (nonEmpty.length > 0 && nonEmpty[0].length < 100) {
    const headline = nonEmpty[0];
    const rest = text.slice(text.indexOf(headline) + headline.length).trim();
    return { headline, body: rest };
  }
  return { body: text };
}

function parseEmailDraft(text: string): { subject?: string; body?: string } {
  // Content Agent returns JSON for email — try to parse
  try {
    const parsed = JSON.parse(text);
    if (parsed.subject && parsed.body) return { subject: parsed.subject, body: parsed.body };
  } catch {/* not JSON, fall through */}
  // Heuristic: first line as subject if it looks like one
  const lines = text.split("\n");
  if (lines[0] && lines[0].length < 80 && !lines[0].endsWith(".")) {
    return { subject: lines[0], body: lines.slice(1).join("\n").trim() };
  }
  return { body: text };
}
