// Creator Discovery: follow YouTube channels through the official YouTube Data API (your own key), see their new
// videos with what YouTube's public data says about them, and import the ones you may clip.
// Every number on this page is one YouTube returned, with the time it was read. Nothing is estimated.
import {
  CheckCircle2, Compass, Download, ExternalLink, EyeOff, KeyRound, Laugh, MessageSquare, Radio, RefreshCw, RotateCcw, Search,
  ShieldAlert, Sparkles, TrendingUp, Trash2, UserPlus,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { SecretField } from "../components/secret";
import {
  Badge, Button, Card, CardHeader, Empty, ErrorNote, Field, Input, Modal, Progress, Segmented, Select, Spinner, Switch, useToast,
} from "../components/ui";
import { api, type DiscoveryCreator, type DiscoveryOverview, type FoundChannel, type FoundVideo, type Job, type Permission } from "../lib/api";
import { ago, clock, cx, duration, PERMISSION_LABELS } from "../lib/format";
import { analyzeDefaults, useDiscovery, useFoundVideos, useRefreshAll, useSettings } from "../lib/hooks";

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const CAN_CLIP: Permission[] = ["owned", "licensed", "permission"];
const RIGHTS: { value: Permission; label: string }[] = [
  { value: "unknown", label: "Not recorded yet" },
  { value: "owned", label: "It is my own channel" },
  { value: "licensed", label: "Licence or clipping campaign" },
  { value: "permission", label: "The creator said I may" },
  { value: "denied", label: "Not allowed" },
];
const QUALITIES = [
  { value: 720, label: "Up to 720p (smaller, faster)" },
  { value: 1080, label: "Up to 1080p (recommended)" },
  { value: 1440, label: "Up to 1440p" },
  { value: 2160, label: "Up to 4K (large, slow)" },
];
const CHECK_HOURS = [
  { value: 0, label: "Only when I press Check" },
  { value: 1, label: "Every hour" },
  { value: 3, label: "Every 3 hours" },
  { value: 6, label: "Every 6 hours" },
  { value: 12, label: "Every 12 hours" },
  { value: 24, label: "Once a day" },
];

function plural(count: number, word: string): string {
  return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
}

/** A comment without the time it starts with: the time is already shown next to it. */
function withoutTime(text: string): string {
  const rest = text.replace(/^\s*(\d{1,2}:)?\d{1,3}:\d{2}\s*[-–—:.,]?\s*/, "");
  return rest || text;
}

function Avatar({ src, name, size = 40 }: { src: string; name: string; size?: number }) {
  // The picture comes straight from YouTube's image servers; nothing about this page is sent with the request.
  // A picture that is gone gives way to the channel's first letter.
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return src && !failed
    ? <img src={src} alt="" width={size} height={size} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} className="shrink-0 rounded-full border border-line bg-raised object-cover" style={{ width: size, height: size }} />
    : <span className="flex shrink-0 items-center justify-center rounded-full border border-line bg-raised text-sm text-muted" style={{ width: size, height: size }}>{name.slice(0, 1).toUpperCase()}</span>;
}

// ------------------------------------------------------------------------------------------------ the key
function KeyCard({ stored, onChanged }: { stored: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const test = async () => {
    setBusy(true);
    setResult(null);
    try {
      await api.post("/discovery/try-key");
      setResult({ ok: true, text: "YouTube answered: the key works." });
    } catch (e) {
      const hint = (e as { hint?: string }).hint;
      setResult({ ok: false, text: (e as Error).message + (hint ? ` ${hint}` : "") });
    } finally {
      setBusy(false);
      onChanged();
    }
  };
  return (
    <Card>
      <CardHeader title={stored ? "Your YouTube API key" : "Set it up: a free YouTube API key"} icon={<KeyRound className="h-4 w-4 text-faint" />}
        hint={stored ? "Stored encrypted on this PC. It is sent only to YouTube, in a request header, and is never shown again."
          : "Creator Discovery asks YouTube's official API, with a key from your own Google account. It is free for this amount of use."} />
      <div className="space-y-4 px-5 pb-5">
        {!stored && (
          <ol className="list-decimal space-y-1.5 pl-5 text-[13.5px] text-muted">
            <li>Open <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noreferrer" className="text-accent-soft hover:underline">Google Cloud</a> and create a project (any name; no payment details are needed).</li>
            <li>Open <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noreferrer" className="text-accent-soft hover:underline">YouTube Data API v3</a> in that project and press <span className="text-ink">Enable</span>.</li>
            <li>Under <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer" className="text-accent-soft hover:underline">APIs &amp; Services → Credentials</a>, press <span className="text-ink">Create credentials → API key</span>. You can restrict it to the YouTube Data API v3.</li>
            <li>Paste the key below.</li>
          </ol>
        )}
        <SecretField name="youtube.api_key" label="YouTube Data API key" stored={stored} onSaved={() => { setResult(null); onChanged(); }} />
        {stored && (
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" onClick={test} busy={busy} title="Asks YouTube for one public channel. Costs 1 of your daily units.">Try the key</Button>
            {result && <p className={cx("text-[13px]", result.ok ? "text-good" : "text-bad")} role="status">{result.text}</p>}
          </div>
        )}
        <p className="text-xs text-faint">
          A free project gets 10,000 units a day. Following a channel costs about 10, looking at it again about 5, and searching
          by name 100. Only public information is asked for: channels, their uploads, their public numbers and top comments.
        </p>
      </div>
    </Card>
  );
}

// ----------------------------------------------------------------------------------------- find and follow
function FindChannel({ overview, onFollowed }: { overview: DiscoveryOverview; onFollowed: () => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"find" | "search" | null>(null);
  const [following, setFollowing] = useState<string | null>(null);
  const [found, setFound] = useState<FoundChannel[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const find = async (search: boolean, value = text) => {
    if (!value.trim()) return;
    setBusy(search ? "search" : "find");
    setError(null);
    try {
      const result = await api.post<{ channels: FoundChannel[]; units: number }>("/discovery/find", { text: value, search });
      setFound(result.channels);
    } catch (e) {
      setFound(null);
      setError(e);
    } finally {
      setBusy(null);
      onFollowed();   // the units used changed
    }
  };

  const follow = async (channel: FoundChannel) => {
    setFollowing(channel.id);
    try {
      const result = await api.post<{ creator: DiscoveryCreator; first_look: { new?: number; error?: string } }>("/discovery/creators", { channel_id: channel.id });
      setFound((list) => list?.map((c) => (c.id === channel.id ? { ...c, creator_id: result.creator.id } : c)) ?? null);
      if (result.first_look.error) toast({ tone: "warn", title: `Following ${channel.title}`, body: `Its uploads could not be looked at yet: ${result.first_look.error}` });
      else toast({ tone: "good", title: `Following ${channel.title}`, body: `${plural(result.first_look.new ?? 0, "video")} to look at below.` });
    } catch (e) {
      toast({ tone: "bad", title: `Could not follow ${channel.title}`, body: (e as Error).message });
    } finally {
      setFollowing(null);
      onFollowed();
    }
  };

  return (
    <Card>
      <CardHeader title="Find a channel" icon={<Search className="h-4 w-4 text-faint" />}
        hint="Paste the channel's link, its @handle, or a link to one of its videos. That costs 1 or 2 units." />
      <div className="space-y-3 px-5 pb-5">
        <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); find(false); }}>
          <Input value={text} onChange={(event) => setText(event.target.value)} className="min-w-[240px] flex-1" spellCheck={false}
            placeholder="https://www.youtube.com/@name   or   @name" aria-label="Channel link, @handle or name" />
          <Button variant="primary" type="submit" busy={busy === "find"} disabled={!text.trim() || busy !== null}>Find channel</Button>
          <Button onClick={() => find(true)} busy={busy === "search"} disabled={!text.trim() || busy !== null}
            title="Looks for channels by name. This is YouTube's expensive request: 100 of your 10,000 daily units.">Search by name (100 units)</Button>
        </form>
        <ErrorNote error={error} />
        {overview.suggestions.length > 0 && !found && (
          <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <span>Already in your library:</span>
            {overview.suggestions.map((s) => (
              <Button key={s.name} size="sm" onClick={() => find(false, s.link)} disabled={busy !== null}
                title={`Looks the channel up from one of its videos you imported (2 units)`}>
                {s.name} <span className="text-faint">· {plural(s.videos, "video")}</span>
              </Button>
            ))}
          </div>
        )}
        {found && (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {found.map((channel) => (
              <li key={channel.id} className="flex items-center gap-3 p-3">
                <Avatar src={channel.thumb_url} name={channel.title} />
                <div className="min-w-0 flex-1">
                  <a href={channel.url} target="_blank" rel="noreferrer" className="block truncate text-sm font-medium text-ink hover:text-accent-soft">{channel.title}</a>
                  <p className="truncate text-xs text-muted">
                    {[channel.handle, channel.subscribers === null ? "subscribers hidden" : `${compact.format(channel.subscribers)} subscribers`,
                      channel.video_count === null ? "" : plural(channel.video_count, "video")].filter(Boolean).join(" · ")}
                  </p>
                </div>
                {channel.creator_id !== null
                  ? <Badge tone="good"><CheckCircle2 className="h-3 w-3" />Following</Badge>
                  : <Button size="sm" variant="primary" onClick={() => follow(channel)} busy={following === channel.id} disabled={following !== null}><UserPlus className="h-4 w-4" />Follow</Button>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function CreatorCard({ creator, onChanged }: { creator: DiscoveryCreator; onChanged: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState(creator.permission_note);
  const [busy, setBusy] = useState<"check" | "remove" | null>(null);
  useEffect(() => setNote(creator.permission_note), [creator.id, creator.permission_note]);

  const save = async (body: Record<string, unknown>) => {
    try {
      await api.patch(`/discovery/creators/${creator.id}`, body);
      onChanged();
    } catch (e) {
      toast({ tone: "bad", title: "Could not save that", body: (e as Error).message });
    }
  };
  const check = async () => {
    setBusy("check");
    try {
      const result = await api.post<{ summary: { new: number } }>(`/discovery/creators/${creator.id}/check`);
      toast({ tone: "good", title: `Looked at ${creator.title}`, body: result.summary.new ? `${plural(result.summary.new, "new video")}.` : "Nothing new since last time." });
    } catch (e) {
      toast({ tone: "bad", title: `Could not look at ${creator.title}`, body: (e as Error).message });
    } finally {
      setBusy(null);
      onChanged();
    }
  };
  const unfollow = async () => {
    if (!window.confirm(`Stop following ${creator.title}? What was found on the channel is removed from this page. Videos you imported stay in your library.`)) return;
    setBusy("remove");
    try {
      await api.del(`/discovery/creators/${creator.id}`);
      onChanged();
    } catch (e) {
      toast({ tone: "bad", title: "Could not unfollow", body: (e as Error).message });
      setBusy(null);
    }
  };

  return (
    <li className="space-y-3 rounded-xl border border-line bg-bg/60 p-3.5">
      <div className="flex items-start gap-3">
        <Avatar src={creator.thumb_url} name={creator.title} size={44} />
        <div className="min-w-0 flex-1">
          <a href={creator.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-sm font-medium text-ink hover:text-accent-soft">
            <span className="truncate">{creator.title}</span><ExternalLink className="h-3 w-3 shrink-0 text-faint" />
          </a>
          <p className="truncate text-xs text-muted">
            {[creator.handle, creator.subscribers === null ? "subscribers hidden" : `${compact.format(creator.subscribers)} subscribers`].filter(Boolean).join(" · ")}
          </p>
          <p className="mt-0.5 text-xs text-faint">
            {creator.checked_at ? `Looked at ${ago(creator.checked_at)}` : "Not looked at yet"} · {plural(creator.videos_listed, "video")} listed
          </p>
        </div>
      </div>
      {creator.check_error && <p className="flex items-start gap-1.5 text-xs text-warn"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{creator.check_error}</p>}
      <Field label="Your right to clip this creator's videos" hint="Finding a video does not make it yours to repost. This record is used when you import one, and goes into every clip's post text.">
        <Select value={creator.permission_status} onChange={(event) => save({ permission_status: event.target.value })} aria-label={`Your right to clip ${creator.title}`}>
          {RIGHTS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </Select>
      </Field>
      {creator.permission_status !== "unknown" && creator.permission_status !== "denied" && (
        <Input value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} aria-label="Where the permission comes from"
          onBlur={() => note.trim() !== creator.permission_note && save({ permission_note: note })}
          placeholder="Where it comes from: campaign page, message from the creator…" />
      )}
      <div className="flex gap-2">
        <Button size="sm" onClick={check} busy={busy === "check"} disabled={busy !== null} title="Looks at the channel's newest uploads now (about 5 units)"><RefreshCw className="h-3.5 w-3.5" />Look now</Button>
        <Button size="sm" variant="ghost" onClick={unfollow} busy={busy === "remove"} disabled={busy !== null}><Trash2 className="h-3.5 w-3.5" />Unfollow</Button>
      </div>
    </li>
  );
}

// -------------------------------------------------------------------------------------------- found videos
function ImportDialog({ video, onClose, onStarted }: { video: FoundVideo | null; onClose: () => void; onStarted: () => void }) {
  const settings = useSettings();
  const toast = useToast();
  const [rights, setRights] = useState("");
  const [note, setNote] = useState("");
  const [remember, setRemember] = useState(true);
  const [quality, setQuality] = useState(1080);
  const [thenClip, setThenClip] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { setRights(""); setNote(""); setRemember(true); setError(null); }, [video?.id]);
  if (!video) return null;

  const recorded = CAN_CLIP.includes(video.creator_rights);
  const denied = video.creator_rights === "denied";
  const values = settings.data?.values ?? {};
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post<Job>(`/discovery/videos/${video.id}/import`, {
        ...(recorded ? {} : { permission_status: rights, permission_note: note, remember }),
        max_height: quality, analyze: thenClip ? analyzeDefaults(values) : null,
      });
      toast({ tone: "good", title: "Import started", body: thenClip ? "It downloads, then ClipForge finds the clips." : "It downloads into your library." });
      onStarted();
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} title="Import this video"
      footer={(
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={start} busy={busy} disabled={denied || (!recorded && !rights)}><Download className="h-4 w-4" />Import video</Button>
        </>
      )}>
      <div className="space-y-4">
        <div>
          <p className="text-sm font-medium text-ink">{video.title}</p>
          <p className="mt-0.5 text-xs text-muted">{video.creator_title} · {duration(video.duration)}</p>
        </div>
        {denied ? (
          <p className="flex items-start gap-1.5 text-[13px] text-bad"><ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />You marked {video.creator_title} as not allowed for clipping. Change that on the channel's card if it is wrong.</p>
        ) : recorded ? (
          <p className="rounded-lg border border-line bg-bg/60 px-3 py-2 text-[13px] text-muted">
            Your right to clip {video.creator_title}, as you recorded it: <span className="text-ink">{PERMISSION_LABELS[video.creator_rights]}</span>. It is saved with the video and added to every clip's post text.
          </p>
        ) : (
          <>
            <Field label="Why may you clip this video?" hint="A video being public does not make it free to repost. Your answer is saved with the video and added to every clip's post text.">
              <Select value={rights} onChange={(event) => setRights(event.target.value)} aria-label="Why may you clip this video?">
                <option value="" disabled>Choose…</option>
                <option value="owned">It is my own content</option>
                <option value="licensed">I have a licence, or it is a campaign</option>
                <option value="permission">The creator said I may</option>
              </Select>
            </Field>
            <Field label="Where the permission comes from (optional)">
              <Input value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} placeholder="e.g. campaign page, message from the creator" />
            </Field>
            <Switch checked={remember} onChange={setRemember} label={`Remember this for all of ${video.creator_title}'s videos`} hint="Off: it is only said for this one video." />
          </>
        )}
        <Field label="Quality to download">
          <Select value={quality} onChange={(event) => setQuality(Number(event.target.value))} aria-label="Quality to download">
            {QUALITIES.map((q) => <option key={q.value} value={q.value}>{q.label}</option>)}
          </Select>
        </Field>
        <Switch checked={thenClip} onChange={setThenClip} label="Find clips as soon as it has downloaded"
          hint={`Uses your defaults from Settings (${String(values["clips.count"] ?? 5)} clips, ${String(values["clips.length"] ?? "15-60")} seconds). Off: it just lands in your library.`} />
        {(video.signals?.moments.length ?? 0) > 0 && thenClip && (
          <p className="text-xs text-faint">Claude is also told the {plural(video.signals?.moments.length ?? 0, "moment")} viewers wrote down in the comments, as hints.</p>
        )}
        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}

function VideoRow({ video, onImport, onChanged }: { video: FoundVideo; onImport: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<"hide" | "comments" | null>(null);
  const act = async (what: "hide" | "comments", run: () => Promise<unknown>, failed: string) => {
    setBusy(what);
    try {
      await run();
    } catch (e) {
      toast({ tone: "bad", title: failed, body: (e as Error).message });
    } finally {
      setBusy(null);
      onChanged();
    }
  };
  const said = video.signals;
  const share = said && said.checked > 0 ? said.laughing / said.checked : 0;
  const finished = video.live === "none";
  const mine = video.library;

  return (
    <li className="flex flex-col gap-3 p-3.5 sm:flex-row">
      <a href={video.url} target="_blank" rel="noreferrer" className="relative block w-full shrink-0 self-start overflow-hidden rounded-lg border border-line bg-black sm:w-[176px]" title="Open on YouTube">
        {video.thumb_url
          ? <img src={video.thumb_url} alt="" loading="lazy" referrerPolicy="no-referrer" className="aspect-video w-full object-cover" />
          : <span className="block aspect-video w-full" />}
        {finished && video.duration > 0 && <span className="absolute bottom-1 right-1 rounded bg-black/80 px-1 text-[11px] tabular text-white">{clock(video.duration)}</span>}
      </a>
      <div className="min-w-0 flex-1 space-y-1.5">
        <a href={video.url} target="_blank" rel="noreferrer" className="line-clamp-2 text-sm font-medium text-ink hover:text-accent-soft">{video.title}</a>
        <p className="text-xs text-muted">
          {[video.creator_title, video.published_at ? ago(video.published_at) : "",
            video.views === null ? "" : `${compact.format(video.views)} views${video.fresh ? " so far" : ""}`,
            video.likes === null ? "" : `${compact.format(video.likes)} likes`,
            video.comments === null ? "" : `${compact.format(video.comments)} comments`].filter(Boolean).join(" · ")}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {video.live === "live" && <Badge tone="bad"><Radio className="h-3 w-3" />Live now</Badge>}
          {video.live === "upcoming" && <Badge>Not started yet</Badge>}
          {video.was_live && finished && <Badge>Stream recording</Badge>}
          {video.standout !== null && (
            <Badge tone={video.standout >= 1.5 ? "good" : "neutral"} title="Its views compared with the middle value of this channel's other long videos, from YouTube's public counts">
              <TrendingUp className="h-3 w-3" />{video.standout}× the channel's usual views
            </Badge>
          )}
          {said && !said.off && said.checked > 0 && (
            <Badge tone={share >= 0.3 && said.checked >= 20 ? "good" : "neutral"} title="Counted in the top comments as YouTube ranks them: laughing emoji, lol, lmao, haha and the like">
              <Laugh className="h-3 w-3" />{said.laughing} of {said.checked} top comments laugh
            </Badge>
          )}
          {said?.off && <Badge><MessageSquare className="h-3 w-3" />Comments are off</Badge>}
          {said && !said.off && said.checked === 0 && !video.comments && <Badge><MessageSquare className="h-3 w-3" />No comments yet</Badge>}
        </div>
        {said && said.moments.length > 0 && (
          <div className="text-[12.5px] text-muted">
            <p className="text-faint">Moments viewers wrote down in the comments:</p>
            <ul className="mt-0.5 space-y-0.5">
              {said.moments.slice(0, 3).map((moment) => (
                <li key={moment.t} className="flex gap-2">
                  <a href={moment.url} target="_blank" rel="noreferrer" className="shrink-0 tabular text-accent-soft hover:underline" title="Open YouTube at this moment">{clock(moment.t)}</a>
                  <span className="min-w-0 truncate" title={moment.text}>
                    <span className="text-faint">{plural(moment.mentions, "comment")}, {plural(moment.likes, "like")}:</span> “{withoutTime(moment.text)}”
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {mine && (
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
            <CheckCircle2 className="h-3.5 w-3.5 text-good" />
            {mine.status === "analyzing" ? <span className="flex items-center gap-1.5">In your library. Finding clips…<Spinner className="h-3 w-3" /></span>
              : mine.clips > 0 ? (
                <span>
                  In your library: {plural(mine.clips, "clip")}{mine.best !== null ? `, best score ${Math.round(mine.best)}` : ""}
                  {mine.funny > 0 ? ` · ${mine.funny} funny (best ${Math.round(mine.best_funny ?? 0)})` : " · none marked funny"}
                </span>
              ) : <span>In your library, no clips yet.</span>}
            <Link to={`/clipper?video=${mine.video_id}`} className="text-accent-soft hover:underline">Open in the AI Clipper</Link>
            {mine.funny > 0 && <Link to={`/library?video=${mine.video_id}&category=funny`} className="text-accent-soft hover:underline">Its funny clips</Link>}
          </p>
        )}
        {video.importing && (
          <div>
            <p className="text-xs text-muted">{video.importing.message || (video.importing.status === "queued" ? "Waiting for the worker…" : "Importing…")}</p>
            <Progress value={video.importing.progress} busy className="mt-1" />
          </div>
        )}
        <p className="text-[11px] text-faint">YouTube's public numbers, read {ago(video.stats_at)}{said?.fetched_at ? `; comments read ${ago(said.fetched_at)}` : ""}.</p>
        <div className="flex flex-wrap gap-2 pt-1">
          {!mine && !video.importing && (
            <Button variant="primary" size="sm" onClick={onImport} disabled={!finished}
              title={finished ? "Downloads it into your library; your right to clip it is asked first" : "A stream can be imported once it has ended"}>
              <Sparkles className="h-4 w-4" />Import and find clips
            </Button>
          )}
          {finished && !said && video.duration >= 120 && (
            <Button size="sm" busy={busy === "comments"} disabled={busy !== null} title="Reads this video's top comments now (1 unit)"
              onClick={() => act("comments", () => api.post(`/discovery/videos/${video.id}/comments`), "Could not read the comments")}>
              <MessageSquare className="h-3.5 w-3.5" />Read its comments
            </Button>
          )}
          {video.status === "new"
            ? <Button size="sm" variant="ghost" busy={busy === "hide"} disabled={busy !== null} onClick={() => act("hide", () => api.patch(`/discovery/videos/${video.id}`, { status: "dismissed" }), "Could not hide it")}><EyeOff className="h-3.5 w-3.5" />Hide</Button>
            : <Button size="sm" variant="ghost" busy={busy === "hide"} disabled={busy !== null} onClick={() => act("hide", () => api.patch(`/discovery/videos/${video.id}`, { status: "new" }), "Could not bring it back")}><RotateCcw className="h-3.5 w-3.5" />Show again</Button>}
        </div>
      </div>
    </li>
  );
}

function FoundVideos({ overview }: { overview: DiscoveryOverview }) {
  const [creator, setCreator] = useState("");
  const [show, setShow] = useState<"new" | "library" | "hidden" | "all">("new");
  const [sort, setSort] = useState("newest");
  const [short, setShort] = useState(false);
  const [importing, setImporting] = useState<FoundVideo | null>(null);
  const filters = useMemo(() => ({
    creator_id: creator ? Number(creator) : undefined, show, sort, min_minutes: short ? 0 : 2,
  }), [creator, show, sort, short]);
  const videos = useFoundVideos(filters);
  const refresh = useRefreshAll();
  const items = videos.data ?? [];

  return (
    <Card>
      <CardHeader title="Their videos" icon={<Sparkles className="h-4 w-4 text-faint" />}
        hint="A found video is a suggestion, not something you may post. Importing it asks for your right to clip it first." />
      <div className="space-y-3 px-5 pb-5">
        <div className="flex flex-wrap items-center gap-2">
          <Segmented value={show} onChange={setShow} options={[{ value: "new", label: "New" }, { value: "library", label: "In my library" }, { value: "hidden", label: "Hidden" }, { value: "all", label: "All" }]} />
          <Select value={overview.creators.some((c) => String(c.id) === creator) ? creator : ""} onChange={(event) => setCreator(event.target.value)} className="w-auto! max-w-[220px]" aria-label="Channel">
            <option value="">All channels</option>
            {overview.creators.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </Select>
          <Select value={sort} onChange={(event) => setSort(event.target.value)} className="w-auto!" aria-label="Order">
            <option value="newest">Newest first</option>
            <option value="laughs">Most laughing comments</option>
            <option value="moments">Most pointed-at moments</option>
            <option value="standout">Standing out most</option>
          </Select>
          <label className="flex items-center gap-2 text-[13px] text-muted">
            <input type="checkbox" className="h-4 w-4 accent-[var(--color-accent)]" checked={short} onChange={(event) => setShort(event.target.checked)} />
            Show videos under 2 minutes
          </label>
        </div>
        {videos.isLoading && <div className="flex justify-center py-10"><Spinner className="h-6 w-6" /></div>}
        <ErrorNote error={videos.error} />
        {!videos.isLoading && items.length === 0 && (
          <Empty title={show === "new" ? "Nothing new to look at" : "Nothing here"}>
            {show === "new" ? "New uploads of the channels you follow appear here. Press Check for new videos to look now." : "Try another list above."}
          </Empty>
        )}
        {items.length > 0 && (
          <ul className="divide-y divide-line rounded-xl border border-line">
            {items.map((video) => <VideoRow key={video.id} video={video} onImport={() => setImporting(video)} onChanged={refresh} />)}
          </ul>
        )}
      </div>
      <ImportDialog video={importing} onClose={() => setImporting(null)} onStarted={refresh} />
    </Card>
  );
}

// -------------------------------------------------------------------------------------------------- page
export default function DiscoveryPage() {
  const discovery = useDiscovery();
  const refresh = useRefreshAll();
  const toast = useToast();
  const [starting, setStarting] = useState(false);
  const overview = discovery.data;

  if (discovery.isLoading) return <div className="flex justify-center py-20"><Spinner className="h-6 w-6" /></div>;
  if (!overview) return <ErrorNote error={discovery.error ?? new Error("Creator Discovery could not be loaded.")} />;

  const checkAll = async () => {
    setStarting(true);
    try {
      await api.post<Job>("/discovery/check");
      refresh();
    } catch (e) {
      toast({ tone: "bad", title: "Could not start the check", body: (e as Error).message });
    } finally {
      setStarting(false);
    }
  };
  const setHours = async (hours: number) => {
    try {
      await api.put("/settings", { values: { "discovery.check_hours": hours } });
      refresh();
    } catch (e) {
      toast({ tone: "bad", title: "Could not save that", body: (e as Error).message });
    }
  };
  const checking = overview.checking;

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="rounded-3xl border border-line bg-panel px-6 py-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-accent"><Compass className="h-5 w-5" /><h2 className="font-display text-xl font-semibold tracking-tight text-ink">Creator Discovery</h2></div>
            <p className="mt-1.5 max-w-2xl text-[13.5px] text-muted">
              Follow YouTube channels, see their new videos with what viewers say about them, and import the ones you are allowed to clip.
              Everything here comes from YouTube's official API with your own key.
            </p>
          </div>
          {overview.key_set && overview.creators.length > 0 && (
            <Button variant="primary" onClick={checkAll} busy={starting || checking !== null} title="Looks at every channel you follow now">
              {!(starting || checking) && <RefreshCw className="h-4 w-4" />}Check for new videos
            </Button>
          )}
        </div>
        {checking && (
          <div className="mt-4">
            <p className="text-xs text-muted">{checking.message || (checking.status === "queued" ? "Waiting for the job that is running to finish…" : "Checking…")}</p>
            <Progress value={checking.progress} busy className="mt-1.5" />
          </div>
        )}
        {overview.key_set && (
          <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px] text-muted">
            <span title="ClipForge counts its own requests. YouTube's day starts again at midnight Pacific time.">
              About <span className="tabular text-ink">{overview.usage.units.toLocaleString()}</span> of {overview.usage.limit.toLocaleString()} YouTube units used today
            </span>
            <label className="flex items-center gap-2">
              Look for new videos by itself
              <Select value={String(CHECK_HOURS.some((h) => h.value === overview.check_hours) ? overview.check_hours : 6)} onChange={(event) => setHours(Number(event.target.value))} className="h-8! w-auto!" aria-label="Look for new videos by itself">
                {CHECK_HOURS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
              </Select>
            </label>
          </div>
        )}
        {overview.paused && (
          <p className="mt-3 flex items-start gap-1.5 text-[13px] text-warn">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            Looking by itself is paused until {new Date(overview.paused.until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}: {overview.paused.why} Pressing Check for new videos tries again now.
          </p>
        )}
      </div>

      {/* Without a key nothing can be asked of YouTube, but what was already found stays, and can be imported. */}
      {overview.key_set ? <FindChannel overview={overview} onFollowed={refresh} /> : <KeyCard stored={false} onChanged={refresh} />}
      {(overview.key_set || overview.creators.length > 0) && (
        <Card>
          <CardHeader title="Channels you follow" hint={overview.creators.length ? undefined : "None yet. Find a channel above and press Follow."} />
          {overview.creators.length > 0 && (
            <ul className="grid gap-3 px-5 pb-5 md:grid-cols-2">
              {overview.creators.map((creator) => <CreatorCard key={creator.id} creator={creator} onChanged={refresh} />)}
            </ul>
          )}
        </Card>
      )}
      {overview.creators.length > 0 && <FoundVideos overview={overview} />}
      {overview.key_set && <KeyCard stored onChanged={refresh} />}
    </div>
  );
}
