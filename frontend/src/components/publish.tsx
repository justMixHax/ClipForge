// Small shared pieces of the Publishing Studio: platform marks, post status, times.
import { Instagram, Music2, Youtube } from "lucide-react";
import type { PlatformKey, PostStatus } from "../lib/api";
import { cx } from "../lib/format";
import type { Tone } from "./ui";

export const PLATFORM: Record<PlatformKey, { label: string; colour: string; icon: typeof Youtube }> = {
  youtube: { label: "YouTube", colour: "#ff3b3b", icon: Youtube },
  tiktok: { label: "TikTok", colour: "#25f4ee", icon: Music2 },
  instagram: { label: "Instagram", colour: "#e1306c", icon: Instagram },
};

export function PlatformMark({ platform, className }: { platform: PlatformKey; className?: string }) {
  const { icon: Icon, colour, label } = PLATFORM[platform];
  return (
    <span title={label} className={cx("inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md", className)} style={{ background: `${colour}1f`, color: colour }}>
      <Icon className="h-3.5 w-3.5" aria-hidden />
    </span>
  );
}

export const STATUS: Record<PostStatus, { label: string; tone: Tone }> = {
  scheduled: { label: "Waiting", tone: "neutral" },
  publishing: { label: "Uploading", tone: "info" },
  processing: { label: "Processing on the platform", tone: "info" },
  published: { label: "Published", tone: "good" },
  scheduled_on_platform: { label: "Scheduled on YouTube", tone: "accent" },
  private: { label: "Uploaded as private", tone: "warn" },
  in_drafts: { label: "In your TikTok inbox", tone: "warn" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const DOT: Record<Tone, string> = {
  neutral: "bg-faint", accent: "bg-accent", good: "bg-good", warn: "bg-warn", bad: "bg-bad", info: "bg-info",
};

/** The moment a post is about: when it goes live, went live, or was queued. */
export function goLive(post: { scheduled_at: string | null; published_at: string | null; created_at: string }): Date {
  return new Date(post.scheduled_at ?? post.published_at ?? post.created_at);
}

export function when(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** For a datetime-local input: tomorrow at 18:00 on this PC's clock. */
export function tomorrowEvening(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(18, 0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function splitTags(text: string): string[] {
  return text.split(/[\s,]+/).map((t) => t.replace(/^#+/, "")).filter(Boolean).map((t) => `#${t}`);
}
