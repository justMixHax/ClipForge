// Publishing → New post: pick approved clips, pick accounts, check the text (or let Claude suggest it), choose
// now or a time, see a preview, and queue. The backend checks every safeguard again before anything is sent.
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Send, ShieldAlert, WandSparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Clip, type PlatformKey, type PublishOverview, type PublishPost } from "../lib/api";
import { clock, cx } from "../lib/format";
import { useClips } from "../lib/hooks";
import { PLATFORM, PlatformMark, splitTags, tomorrowEvening } from "./publish";
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, Field, Input, Segmented, Select, Spinner, Switch, Textarea, useToast } from "./ui";

type Text = { title: string; caption: string; tags: string };
const RIGHTS_OK = new Set(["owned", "licensed", "permission"]);

function startText(clip: Clip): Text {
  return { title: clip.title.slice(0, 100), caption: clip.description || clip.title, tags: clip.hashtags.join(" ") };
}

export function PublishComposer({ overview, onQueued }: { overview: PublishOverview; onQueued: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const clips = useClips({ review_status: "approved", render_status: "ready", sort: "newest" });
  const accounts = overview.accounts.filter((a) => a.status === "connected");
  const [picked, setPicked] = useState<number[]>([]);
  const [targets, setTargets] = useState<number[]>([]);
  const [texts, setTexts] = useState<Record<number, Text>>({});
  const [privacy, setPrivacy] = useState("public");
  const [kids, setKids] = useState(false);
  const [synthetic, setSynthetic] = useState(false);
  const [notify, setNotify] = useState(true);
  const [feed, setFeed] = useState(true);
  const [timing, setTiming] = useState<"now" | "later">("now");
  const [at, setAt] = useState(tomorrowEvening());
  const [again, setAgain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [suggesting, setSuggesting] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  const all = clips.data?.items ?? [];
  const chosen = all.filter((c) => picked.includes(c.id));
  const platforms = useMemo(() => new Set(accounts.filter((a) => targets.includes(a.id)).map((a) => a.platform)), [accounts, targets]);
  const textOf = (clip: Clip) => texts[clip.id] ?? startText(clip);
  const setText = (clip: Clip, patch: Partial<Text>) => setTexts((current) => ({ ...current, [clip.id]: { ...textOf(clip), ...patch } }));
  const toggle = (list: number[], id: number) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  const total = chosen.length * targets.length;
  const unclear = chosen.filter((c) => !RIGHTS_OK.has(c.permission_status));
  const preview = chosen[0] ?? null;

  const suggest = async (clip: Clip) => {
    const platform: PlatformKey = platforms.has("youtube") ? "youtube" : ([...platforms][0] ?? "youtube");
    setSuggesting(clip.id);
    try {
      const s = await api.post<{ title: string; caption: string; hashtags: string[] }>("/publishing/suggest", { clip_id: clip.id, platform });
      setText(clip, { title: s.title || textOf(clip).title, caption: s.caption || textOf(clip).caption, tags: s.hashtags.join(" ") || textOf(clip).tags });
    } catch (e) {
      toast({ tone: "bad", title: "Claude could not suggest text", body: (e as Error).message });
    } finally {
      setSuggesting(null);
    }
  };

  const queue = async () => {
    setBusy(true);
    setError(null);
    try {
      const scheduled = timing === "later" ? new Date(at).toISOString() : null;
      const items = chosen.flatMap((clip) => accounts.filter((a) => targets.includes(a.id)).map((account) => {
        const text = textOf(clip);
        return {
          clip_id: clip.id, account_id: account.id, title: text.title, caption: text.caption, hashtags: splitTags(text.tags),
          privacy: account.platform === "youtube" ? privacy : "",
          options: account.platform === "youtube" ? { made_for_kids: kids, synthetic_media: synthetic, notify_subscribers: notify }
            : account.platform === "instagram" ? { share_to_feed: feed } : {},
          scheduled_at: scheduled,
        };
      }));
      const posts = await api.post<PublishPost[]>("/publishing/posts", { items, post_again: again });
      client.invalidateQueries({ queryKey: ["posts"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      toast({ tone: "good", title: `${posts.length} post${posts.length === 1 ? "" : "s"} queued`, body: timing === "now" ? "Uploading starts now." : "They go out at the time you chose." });
      setPicked([]);
      setTexts({});
      onQueued();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  if (clips.isLoading) return <div className="flex justify-center p-10"><Spinner /></div>;

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0 space-y-4">
        <Card>
          <CardHeader title="1 · Clips" hint="Approved and rendered clips. Approve more in the AI Clipper or the Clip Library." />
          <div className="px-5 pb-5">
            {all.length === 0 ? (
              <Empty title="No approved, rendered clips yet">Approve a clip and render it, then it shows up here. <Link to="/clipper" className="text-accent-soft hover:underline">Open the AI Clipper</Link></Empty>
            ) : (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {all.map((clip) => {
                  const on = picked.includes(clip.id);
                  return (
                    <button key={clip.id} type="button" onClick={() => setPicked(toggle(picked, clip.id))}
                      className={cx("overflow-hidden rounded-lg border text-left transition-colors", on ? "border-accent bg-accent/10" : "border-line bg-bg/40 hover:border-line-strong")}>
                      <div className="relative aspect-[9/16] max-h-48 w-full bg-black">
                        {(clip.cover_url || clip.thumb_url) && <img src={clip.cover_url || clip.thumb_url} alt="" className="h-full w-full object-cover" />}
                        <span className="absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[11px] text-white">{clock(clip.out_duration || clip.end - clip.start)}</span>
                        {on && <span className="absolute left-1 top-1 rounded bg-accent px-1.5 text-[11px] font-semibold text-[#1a0d05]">Picked</span>}
                      </div>
                      <p className="line-clamp-2 p-2 text-xs text-ink">{clip.title}</p>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="2 · Where" hint="Connected accounts. Connect more under Accounts." />
          <div className="flex flex-wrap gap-2 px-5 pb-5">
            {accounts.length === 0 && <p className="text-[13px] text-muted">No account is connected yet. <Link to="/publishing?tab=accounts" className="text-accent-soft hover:underline">Connect one</Link>.</p>}
            {accounts.map((account) => {
              const on = targets.includes(account.id);
              return (
                <button key={account.id} type="button" onClick={() => setTargets(toggle(targets, account.id))}
                  className={cx("flex items-center gap-2 rounded-lg border px-3 py-2 text-sm", on ? "border-accent bg-accent/10 text-ink" : "border-line bg-bg/40 text-muted hover:text-ink")}>
                  <PlatformMark platform={account.platform} />{account.name}{account.handle && <span className="text-faint">{account.handle}</span>}
                </button>
              );
            })}
          </div>
          {platforms.has("tiktok") && <p className="px-5 pb-5 text-xs text-faint">TikTok receives the video only: it lands in your TikTok inbox, and you paste the caption and post it in the app. The caption is ready to copy from the post in the calendar.</p>}
        </Card>

        {chosen.length > 0 && (
          <Card>
            <CardHeader title="3 · Text" hint="Starts from the clip's own title, description and hashtags. Claude can suggest better ones from what is said in the clip." />
            <div className="space-y-4 px-5 pb-5">
              {chosen.map((clip) => {
                const text = textOf(clip);
                return (
                  <div key={clip.id} className="rounded-xl border border-line bg-bg/40 p-3">
                    <div className="mb-2 flex items-center gap-2">
                      <p className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{clip.title}</p>
                      {!RIGHTS_OK.has(clip.permission_status) && <Badge tone="bad" title="Record your right to post clips of this source first">Right to post not recorded</Badge>}
                      <Button size="sm" onClick={() => suggest(clip)} busy={suggesting === clip.id}><WandSparkles className="h-3.5 w-3.5" />Suggest with Claude</Button>
                    </div>
                    <div className="grid gap-2">
                      {platforms.has("youtube") && (
                        <Field label="YouTube title" hint={`${text.title.length}/100`}>
                          <Input value={text.title} maxLength={100} onChange={(e) => setText(clip, { title: e.target.value })} />
                        </Field>
                      )}
                      <Field label="Caption / description" hint={platforms.has("instagram") ? `${text.caption.length}/2200 on Instagram` : undefined}>
                        <Textarea rows={3} value={text.caption} onChange={(e) => setText(clip, { caption: e.target.value })} />
                      </Field>
                      <Field label="Hashtags" hint={`${splitTags(text.tags).length} hashtags`}>
                        <Input value={text.tags} onChange={(e) => setText(clip, { tags: e.target.value })} placeholder="#funny #gaming" />
                      </Field>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        )}

        {(platforms.has("youtube") || platforms.has("instagram")) && (
          <Card>
            <CardHeader title="4 · Options" />
            <div className="grid gap-3 px-5 pb-5 sm:grid-cols-2">
              {platforms.has("youtube") && (
                <>
                  <Field label="YouTube visibility" hint={timing === "later" ? "Scheduled Shorts stay private until their time, then go public." : undefined}>
                    <Select value={privacy} onChange={(e) => setPrivacy(e.target.value)} disabled={timing === "later"}>
                      <option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option>
                    </Select>
                  </Field>
                  <div className="space-y-2">
                    <Switch checked={kids} onChange={setKids} label="Made for kids" hint="YouTube requires you to say so." />
                    <Switch checked={synthetic} onChange={setSynthetic} label="Altered or synthetic content" hint="Realistic content made or changed with AI." />
                    <Switch checked={notify} onChange={setNotify} label="Notify subscribers" />
                  </div>
                </>
              )}
              {platforms.has("instagram") && <Switch checked={feed} onChange={setFeed} label="Instagram: also show the Reel in the main feed" />}
            </div>
          </Card>
        )}

        <Card>
          <CardHeader title={chosen.length && (platforms.has("youtube") || platforms.has("instagram")) ? "5 · When" : "When"} />
          <div className="space-y-3 px-5 pb-5">
            <Segmented value={timing} onChange={setTiming} options={[{ value: "now", label: "Post now" }, { value: "later", label: "Schedule" }]} />
            {timing === "later" && (
              <Field label="Date and time (this PC's clock)" hint="YouTube publishes a scheduled Short by itself. TikTok and Instagram posts go out at that time while ClipForge is running; if it is closed, they go out when it starts again.">
                <Input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
              </Field>
            )}
            <Switch checked={again} onChange={setAgain} label="Post again anyway" hint="Only if a clip was already posted or queued for one of these accounts and you want a second copy." />
          </div>
        </Card>
      </div>

      <div className="space-y-4 xl:sticky xl:top-4 xl:self-start">
        <Card>
          <CardHeader title="Preview" hint={preview ? (chosen.length > 1 ? `First of ${chosen.length} clips` : undefined) : "Pick a clip to see it here."} />
          <div className="px-5 pb-5">
            {preview ? (
              <div className="mx-auto w-full max-w-[260px] overflow-hidden rounded-[22px] border-4 border-raised bg-black">
                <div className="relative aspect-[9/16]">
                  <video key={preview.id} src={preview.file_url} className="h-full w-full object-cover" controls muted loop playsInline />
                </div>
                <div className="space-y-1 bg-panel p-3 text-xs">
                  {[...platforms].map((p) => <span key={p} className="mr-1 inline-flex items-center gap-1 text-faint"><PlatformMark platform={p} className="h-5 w-5" />{PLATFORM[p].label}</span>)}
                  {platforms.has("youtube") && <p className="font-medium text-ink">{textOf(preview).title}</p>}
                  <p className="whitespace-pre-line text-muted">{textOf(preview).caption}</p>
                  <p className="text-info">{splitTags(textOf(preview).tags).join(" ")}</p>
                </div>
              </div>
            ) : <div className="flex aspect-[9/16] max-h-80 items-center justify-center rounded-xl border border-dashed border-line text-xs text-faint">No clip picked</div>}
          </div>
        </Card>

        {unclear.length > 0 && (
          <p className="flex items-start gap-1.5 rounded-lg border border-bad/30 bg-bad/10 p-3 text-xs text-bad">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {unclear.length === 1 ? "One clip comes" : `${unclear.length} clips come`} from a source whose right to post is not recorded. Record it under Source and rights in the AI Clipper first; until then it is refused.
          </p>
        )}
        <ErrorNote error={error} />
        <Button variant="primary" size="lg" className="w-full justify-center" onClick={queue} busy={busy} disabled={total === 0}>
          {timing === "later" ? <CalendarClock className="h-4 w-4" /> : <Send className="h-4 w-4" />}
          {total === 0 ? "Pick clips and accounts" : `${timing === "later" ? "Schedule" : "Post"} ${total} post${total === 1 ? "" : "s"}`}
        </Button>
        <p className="text-center text-xs text-faint">A post shows as published only once the platform confirms it.</p>
      </div>
    </div>
  );
}
