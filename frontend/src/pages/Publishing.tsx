// The Publishing Studio: a calendar of what goes out when, a composer for new posts, and the accounts it posts
// to. Every status shown comes from the post queue, and "Published" only appears once the platform confirmed it.
import { useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Copy, ExternalLink, RotateCcw, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PublishAccounts } from "../components/PublishAccounts";
import { PublishComposer } from "../components/PublishComposer";
import { DOT, PlatformMark, STATUS, goLive, when } from "../components/publish";
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, Modal, Segmented, Spinner, useToast } from "../components/ui";
import { api, type PublishPost } from "../lib/api";
import { cx } from "../lib/format";
import { usePosts, usePublishing } from "../lib/hooks";

type Tab = "calendar" | "new" | "accounts";
const COMING = new Set(["scheduled", "publishing", "processing", "in_drafts", "scheduled_on_platform"]);
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function usePostActions() {
  const client = useQueryClient();
  const toast = useToast();
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["posts"] });
    client.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const act = (title: string, call: () => Promise<unknown>) => call().then(refresh).catch((e: Error) => toast({ tone: "bad", title, body: e.message }));
  return {
    cancel: (post: PublishPost) => act("Could not cancel", () => api.post(`/publishing/posts/${post.id}/cancel`)),
    retry: (post: PublishPost) => act("Could not try again", () => api.post(`/publishing/posts/${post.id}/retry`)),
    remove: (post: PublishPost) => window.confirm("Remove this post from the list? Nothing is deleted on the platform.")
      && act("Could not remove", () => api.del(`/publishing/posts/${post.id}`)),
  };
}

function PostActions({ post, compact = false }: { post: PublishPost; compact?: boolean }) {
  const actions = usePostActions();
  const size = compact ? "sm" : "md";
  return (
    <div className="flex flex-wrap gap-1.5">
      {post.url && <a href={post.url} target="_blank" rel="noreferrer"><Button size={size}><ExternalLink className="h-3.5 w-3.5" />Open</Button></a>}
      {(post.status === "scheduled" || post.status === "publishing") && <Button size={size} variant="ghost" onClick={() => actions.cancel(post)}><X className="h-3.5 w-3.5" />Cancel</Button>}
      {(post.status === "failed" || post.status === "cancelled") && <Button size={size} onClick={() => actions.retry(post)}><RotateCcw className="h-3.5 w-3.5" />Try again</Button>}
      {post.status !== "publishing" && <Button size={size} variant="ghost" title="Remove from this list" onClick={() => actions.remove(post)}><Trash2 className="h-3.5 w-3.5" /></Button>}
    </div>
  );
}

function PostDetail({ post, onClose }: { post: PublishPost | null; onClose: () => void }) {
  const toast = useToast();
  if (!post) return null;
  const status = STATUS[post.status];
  const text = [post.platform === "youtube" ? post.title : "", post.caption, post.hashtags.join(" ")].filter(Boolean).join("\n\n");
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    toast({ tone: "good", title: "Caption copied", body: post.platform === "tiktok" ? "Paste it into the post in TikTok." : undefined });
  };
  return (
    <Modal open onClose={onClose} title={post.clip_title || post.title || "Post"} footer={<PostActions post={post} />}>
      <div className="flex gap-4">
        <div className="w-28 shrink-0 overflow-hidden rounded-lg border border-line bg-black">
          {post.thumb_url && <img src={post.thumb_url} alt="" className="aspect-[9/16] w-full object-cover" onError={(e) => { e.currentTarget.style.display = "none"; }} />}
        </div>
        <div className="min-w-0 flex-1 space-y-2 text-[13px]">
          <p className="flex items-center gap-2 text-ink"><PlatformMark platform={post.platform} />{post.account_name || "Disconnected account"}</p>
          <Badge tone={status.tone}>{status.label}</Badge>
          {post.detail && <p className="text-muted">{post.detail}</p>}
          {post.error && <ErrorNote error={new Error(post.error)} />}
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-faint">
            {post.scheduled_at && <><dt>Goes live</dt><dd className="text-muted">{when(post.scheduled_at)}</dd></>}
            {post.published_at && <><dt>Published</dt><dd className="text-muted">{when(post.published_at)}</dd></>}
            <dt>Queued</dt><dd className="text-muted">{when(post.created_at)}</dd>
            {post.privacy && <><dt>Visibility</dt><dd className="text-muted">{post.privacy}</dd></>}
            {post.attempts > 1 && <><dt>Attempts</dt><dd className="text-muted">{post.attempts}</dd></>}
          </dl>
        </div>
      </div>
      <div className="mt-4 rounded-lg border border-line bg-bg/60 p-3">
        <div className="mb-1 flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wider text-faint">{post.platform === "tiktok" ? "Caption to paste in TikTok" : "Text"}</p>
          <Button size="sm" variant="ghost" onClick={copy}><Copy className="h-3.5 w-3.5" />Copy</Button>
        </div>
        <p className="whitespace-pre-line text-[13px] text-muted">{text || "No text."}</p>
      </div>
    </Modal>
  );
}

function Calendar({ posts, onOpen }: { posts: PublishPost[]; onOpen: (post: PublishPost) => void }) {
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const today = new Date();
  const days = useMemo(() => {
    const first = new Date(month);
    const offset = (first.getDay() + 6) % 7; // weeks start on Monday
    const start = new Date(first.getFullYear(), first.getMonth(), 1 - offset);
    return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
  }, [month]);
  const shift = (by: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + by, 1));
  return (
    <Card>
      <CardHeader title={month.toLocaleDateString([], { month: "long", year: "numeric" })} icon={<CalendarDays className="h-4 w-4 text-faint" />}
        action={<div className="flex gap-1">
          <Button size="icon" variant="ghost" title="Previous month" onClick={() => shift(-1)}><ChevronLeft className="h-4 w-4" /></Button>
          <Button size="sm" variant="ghost" onClick={() => { const d = new Date(); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); }}>Today</Button>
          <Button size="icon" variant="ghost" title="Next month" onClick={() => shift(1)}><ChevronRight className="h-4 w-4" /></Button>
        </div>} />
      <div className="overflow-x-auto px-5 pb-5">
        <div className="grid min-w-[640px] grid-cols-7 gap-px overflow-hidden rounded-lg border border-line bg-line">
          {WEEKDAYS.map((d) => <div key={d} className="bg-raised px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-faint">{d}</div>)}
          {days.map((day) => {
            const items = posts.filter((p) => sameDay(goLive(p), day)).sort((a, b) => goLive(a).getTime() - goLive(b).getTime());
            const outside = day.getMonth() !== month.getMonth();
            return (
              <div key={day.toISOString()} className={cx("min-h-[92px] bg-panel p-1.5", outside && "opacity-40")}>
                <p className={cx("mb-1 text-[11px] tabular", sameDay(day, today) ? "font-semibold text-accent" : "text-faint")}>{day.getDate()}</p>
                <div className="space-y-1">
                  {items.slice(0, 4).map((post) => (
                    <button key={post.id} type="button" onClick={() => onOpen(post)} title={`${post.clip_title} · ${STATUS[post.status].label}`}
                      className="flex w-full items-center gap-1 rounded bg-raised px-1 py-0.5 text-left text-[11px] text-ink hover:bg-hover">
                      <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", DOT[STATUS[post.status].tone])} />
                      <PlatformMark platform={post.platform} className="h-4 w-4" />
                      <span className="tabular text-faint">{goLive(post).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                      <span className="truncate">{post.clip_title}</span>
                    </button>
                  ))}
                  {items.length > 4 && <p className="px-1 text-[11px] text-faint">+{items.length - 4} more</p>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Card>
  );
}

function PostRows({ posts, onOpen }: { posts: PublishPost[]; onOpen: (post: PublishPost) => void }) {
  return (
    <ul className="divide-y divide-line">
      {posts.map((post) => {
        const status = STATUS[post.status];
        return (
          <li key={post.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
            <button type="button" onClick={() => onOpen(post)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
              <PlatformMark platform={post.platform} />
              <div className="min-w-0">
                <p className="truncate text-sm text-ink">{post.clip_title || post.title}</p>
                <p className="truncate text-xs text-faint">{post.account_name || "Disconnected account"} · {when(goLive(post).toISOString())}{post.detail ? ` · ${post.detail}` : ""}</p>
                {post.error && <p className="truncate text-xs text-bad">{post.error}</p>}
              </div>
            </button>
            <Badge tone={status.tone}>{status.label}</Badge>
            <PostActions post={post} compact />
          </li>
        );
      })}
    </ul>
  );
}

export default function PublishingStudio() {
  const [params, setParams] = useSearchParams();
  const tab = (["calendar", "new", "accounts"].includes(params.get("tab") ?? "") ? params.get("tab") : "calendar") as Tab;
  const overview = usePublishing();
  const posts = usePosts();
  const [openId, setOpenId] = useState<number | null>(null);
  const all = posts.data ?? [];
  const coming = all.filter((p) => COMING.has(p.status)).sort((a, b) => goLive(a).getTime() - goLive(b).getTime());
  const done = all.filter((p) => !COMING.has(p.status)).sort((a, b) => goLive(b).getTime() - goLive(a).getTime()).slice(0, 50);
  const connected = overview.data?.accounts.filter((a) => a.status === "connected").length ?? 0;
  const attention = overview.data?.accounts.some((a) => a.status !== "connected");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Publishing Studio</h1>
          <p className="text-[13px] text-muted">Post approved clips to YouTube Shorts, TikTok drafts and Instagram Reels, now or on a schedule, through each platform's official API and your own sign-in.</p>
        </div>
        <Segmented value={tab} onChange={(next) => setParams(next === "calendar" ? {} : { tab: next })} options={[
          { value: "calendar", label: `Calendar${coming.length ? ` · ${coming.length}` : ""}` },
          { value: "new", label: "New post" },
          { value: "accounts", label: <span className={cx(attention && "text-warn")}>Accounts · {connected}</span> },
        ]} />
      </div>

      {overview.isError && <ErrorNote error={overview.error} />}
      {!overview.data ? <div className="flex justify-center p-10"><Spinner /></div> : tab === "accounts" ? (
        <PublishAccounts overview={overview.data} />
      ) : tab === "new" ? (
        <PublishComposer overview={overview.data} onQueued={() => setParams({})} />
      ) : (
        <>
          {connected === 0 && (
            <Empty icon={<CalendarDays className="h-8 w-8" />} title="Connect an account to start posting"
              action={<Button variant="primary" onClick={() => setParams({ tab: "accounts" })}>Set up accounts</Button>}>
              ClipForge posts through each platform's official API with your own developer app, so it needs a one-time setup per platform. The Accounts tab walks you through it.
            </Empty>
          )}
          <Calendar posts={all} onOpen={(post) => setOpenId(post.id)} />
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title="Coming up and in progress" hint="Waiting, uploading, processing, or in your TikTok inbox." />
              {coming.length ? <PostRows posts={coming} onOpen={(post) => setOpenId(post.id)} /> : <p className="px-5 pb-5 text-[13px] text-faint">Nothing queued. Use New post to queue clips.</p>}
            </Card>
            <Card>
              <CardHeader title="Done" hint="Published, uploaded as private, failed or cancelled. The latest 50." />
              {done.length ? <PostRows posts={done} onOpen={(post) => setOpenId(post.id)} /> : <p className="px-5 pb-5 text-[13px] text-faint">Nothing has gone out yet.</p>}
            </Card>
          </div>
        </>
      )}
      <PostDetail post={all.find((p) => p.id === openId) ?? null} onClose={() => setOpenId(null)} />
    </div>
  );
}
