/**
 * Channel-native preview — a LinkedIn post should look like a LinkedIn post.
 *
 * Each channel renders the draft text in its native chrome so the founder
 * decides on what the world will actually see, not on monospace card text.
 */

import {
  Heart, MessageCircle, Repeat2, Send, ThumbsUp,
  Mail as MailIcon, Share2, BookmarkPlus, MoreHorizontal,
} from "lucide-react";
import { cn } from "@/lib/utils";

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
  const imgs = images && images.length ? images : image ? [image] : [];
  const primary = imgs[0] ?? null;
  const inner =
    channel === "linkedin" ? <LinkedInPreview text={text} image={primary} className={className} />
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
    return (
      <img
        src={image.url}
        alt={image.alt_text}
        className={cn("w-full object-cover", rounded && "rounded-md", className)}
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

function LinkedInPreview({ text, image, className }: { text: string; image?: PreviewImage | null; className?: string }) {
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
        <div className="border-b border-[#e6e9ec] dark:border-[#38434f]">
          <ImageBlock image={image} aspect="1/1" rounded={false} />
        </div>
      )}
      <div className="px-4 py-2 border-b border-[#e6e9ec] dark:border-[#38434f] flex items-center justify-between text-[12px] text-[#666] dark:text-[#a0a8af]">
        <div className="flex items-center gap-1">
          <ThumbsUp className="h-3.5 w-3.5 fill-[#0a66c2] text-[#0a66c2]" />
          <span>—</span>
        </div>
        <span>— comments · — reposts</span>
      </div>
      <div className="px-2 py-1 grid grid-cols-4 gap-1 text-[#666] dark:text-[#a0a8af] text-[13px]">
        {[
          { icon: ThumbsUp, label: "Like" },
          { icon: MessageCircle, label: "Comment" },
          { icon: Repeat2, label: "Repost" },
          { icon: Send, label: "Send" },
        ].map(({ icon: Icon, label }) => (
          <button key={label} className="flex items-center justify-center gap-1 py-2 hover:bg-[#f3f6f8] dark:hover:bg-[#283038] rounded">
            <Icon className="h-4 w-4" />
            <span className="font-medium">{label}</span>
          </button>
        ))}
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

function BlogPreview({ text, image, className }: { text: string; image?: PreviewImage | null; className?: string }) {
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
      <div className="prose-memo px-8 py-6 max-w-none">
        {text.split("\n").map((line, i) => {
          const trimmed = line.trim();
          if (trimmed.startsWith("## ")) return <h2 key={i}>{trimmed.slice(3)}</h2>;
          if (trimmed.startsWith("# ")) return <h2 key={i} className="text-3xl mt-2">{trimmed.slice(2)}</h2>;
          if (trimmed.startsWith("- ")) return <li key={i} className="ml-5 list-disc">{trimmed.slice(2)}</li>;
          if (!trimmed) return <br key={i} />;
          return <p key={i}>{trimmed}</p>;
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
          <span className="ml-auto flex items-center gap-3">
            <BookmarkPlus className="h-3.5 w-3.5" />
            <Share2 className="h-3.5 w-3.5" />
            <MoreHorizontal className="h-3.5 w-3.5" />
          </span>
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
              return <h2 key={i} className="font-serif text-[22px] font-semibold mt-7 mb-3">{trimmed.slice(3)}</h2>;
            if (trimmed.startsWith("# "))
              return <h2 key={i} className="font-serif text-[24px] font-semibold mt-7 mb-3">{trimmed.slice(2)}</h2>;
            if (trimmed.startsWith("> "))
              return (
                <blockquote key={i} className="border-l-4 border-[#ff6719] pl-4 my-4 italic text-muted-foreground">
                  {trimmed.slice(2)}
                </blockquote>
              );
            if (trimmed.startsWith("- "))
              return <li key={i} className="ml-6 list-disc my-1">{trimmed.slice(2)}</li>;
            return <p key={i} className="mb-4">{trimmed}</p>;
          })}
        </div>
      </article>

      {/* Reaction row — Substack-style */}
      <div className="border-t px-6 py-3 flex items-center justify-between text-[13px] text-muted-foreground">
        <div className="flex items-center gap-4">
          <button className="inline-flex items-center gap-1.5 hover:text-foreground transition-colors">
            <Heart className="h-4 w-4" />
            <span>Like</span>
          </button>
          <button className="inline-flex items-center gap-1.5 hover:text-foreground transition-colors">
            <MessageCircle className="h-4 w-4" />
            <span>Comment</span>
          </button>
          <button className="inline-flex items-center gap-1.5 hover:text-foreground transition-colors">
            <Repeat2 className="h-4 w-4" />
            <span>Restack</span>
          </button>
        </div>
        <button className="inline-flex items-center gap-1.5 hover:text-foreground transition-colors">
          <Share2 className="h-4 w-4" />
        </button>
      </div>
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
  // Heuristic: first non-empty line as headline, optional second line as subtitle
  const lines = text.split("\n").map((l) => l.trim());
  const nonEmpty = lines.filter(Boolean);
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
