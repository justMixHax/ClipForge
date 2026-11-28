// The main workspace: pick a video, choose how to clip it, press Generate, review the ranked results.
import { useQueryClient } from "@tanstack/react-query";
import { Bookmark, BookmarkPlus, FolderOpen, HardDrive, RotateCcw, ShieldAlert, Sparkles, UploadCloud, Wand2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ClipCard, ClipViewer, RightsBadge, SourcePeek, useClipActions } from "../components/clips";
import { FocusPicker } from "../components/FocusPicker";
import { JobStages } from "../components/JobStages";
import { LinkImport } from "../components/LinkImport";
import { MomentClip, emptyMoment, markMoment, type MomentTimes } from "../components/MomentClip";
import { VideoWorkLog } from "../components/WorkLog";
import { ProjectSelect } from "../components/projects";
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, Field, Input, Progress, Segmented, Select, Switch, useToast } from "../components/ui";
import { api, uploadVideo, type Clip, type Job, type SavedPreset, type Video } from "../lib/api";
import { bytes, clock, cx, duration } from "../lib/format";
import { useActiveJobs, useClips, useOptions, usePresets, useRefreshAll, useSettings, useVideo, useVideos } from "../lib/hooks";

type ClipOptions = {
  workflow: string;
  content_preset: string;
  game_style: string;
  focus: string;      // the kind of moment to look for
  look: boolean;      // Claude's second look also gets frames of each pick
  count: number;
  length: string;
  min_score: number;
  aspect: string;
  layout: string;
  captions: string;
  quality: string;
  auto_render: boolean;
  remove_silence: boolean;
  show_hook: boolean;
};
type Kind = Pick<ClipOptions, "focus" | "look"> & { onFocus: (key: string) => void; onLook: (on: boolean) => void };

const DEFAULTS: ClipOptions = {
  workflow: "custom", content_preset: "auto", game_style: "", focus: "best", look: false, count: 5, length: "15-60", min_score: 55, aspect: "9:16",
  layout: "auto", captions: "opus", quality: "balanced", auto_render: true, remove_silence: false, show_hook: true,
};
const VIDEO_TYPES = [".mp4", ".mov", ".mkv", ".webm", ".m4v"];

function SourcePicker({ onPicked, onImported, analyzeBody, kind }: {
  onPicked: (video: Video) => void;
  onImported: (videoId: number) => void;
  analyzeBody: () => Record<string, unknown>;
  kind: Kind;
}) {
  const videos = useVideos();
  const options = useOptions();
  const refresh = useRefreshAll();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [upload, setUpload] = useState<{ name: string; progress: number; cancel: () => void } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [path, setPath] = useState("");
  const [linking, setLinking] = useState(false);

  const start = (file: File) => {
    setError(null);
    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!VIDEO_TYPES.includes(extension)) {
      setError(new Error(`“${file.name}” is not a supported video. Use MP4, MOV, MKV, WebM or M4V.`));
      return;
    }
    const limit = (options.data?.max_upload_gb ?? 20) * 1024 ** 3;
    if (file.size > limit) {
      setError(new Error(`This file is ${bytes(file.size)}, over the ${options.data?.max_upload_gb ?? 20} GB upload limit. Use “Add from disk” below instead: it links the file without copying it.`));
      return;
    }
    const { promise, cancel } = uploadVideo(file, (progress) => setUpload((u) => (u ? { ...u, progress } : u)));
    setUpload({ name: file.name, progress: 0, cancel });
    promise
      .then((video) => { refresh(); onPicked(video); })
      .catch((e) => setError(e))
      .finally(() => setUpload(null));
  };

  const link = async () => {
    setError(null);
    setLinking(true);
    try {
      const video = await api.post<Video>("/videos/import-path", { path });
      refresh();
      onPicked(video);
    } catch (e) {
      setError(e);
    } finally {
      setLinking(false);
    }
  };

  return (
    <div className="space-y-4">
      <div
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (file) start(file); }}
        className={cx("rounded-2xl border-2 border-dashed px-5 py-8 text-center transition-colors", dragging ? "border-accent bg-accent/5" : "border-line hover:border-line-strong")}
      >
        {upload ? (
          <div className="space-y-3">
            <p className="truncate text-sm text-ink">Uploading {upload.name}</p>
            <Progress value={upload.progress} busy />
            <div className="flex items-center justify-between text-xs text-muted">
              <span className="tabular">{Math.round(upload.progress * 100)}%{upload.progress >= 1 && " · checking the file…"}</span>
              <button type="button" className="hover:text-ink" onClick={upload.cancel}>Cancel</button>
            </div>
          </div>
        ) : (
          <>
            <UploadCloud className="mx-auto h-8 w-8 text-faint" />
            <p className="mt-2 text-sm text-ink">Drop a video here</p>
            <p className="text-xs text-faint">MP4, MOV, MKV, WebM · up to {options.data?.max_upload_gb ?? 20} GB</p>
            <Button className="mt-3" onClick={() => input.current?.click()}>Choose a file</Button>
            <input ref={input} type="file" accept={VIDEO_TYPES.join(",") + ",video/*"} hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) start(file); event.target.value = ""; }} />
          </>
        )}
      </div>
      <ErrorNote error={error} />

      <LinkImport analyzeBody={analyzeBody} onImported={onImported} kind={<FocusPicker {...kind} />} />

      <Field label="Or add a video that is already on this PC" hint="Links the file where it is. Nothing is copied, and ClipForge never changes or deletes your original.">
        <div className="flex gap-2">
          <Input value={path} onChange={(event) => setPath(event.target.value)} placeholder="C:\Videos\stream.mp4" spellCheck={false} onKeyDown={(event) => event.key === "Enter" && path.trim() && link()} />
          <Button onClick={link} busy={linking} disabled={!path.trim()}><FolderOpen className="h-4 w-4" />Add</Button>
        </div>
      </Field>

      {(videos.data?.length ?? 0) > 0 && (
        <Field label="Or continue with a video from your library">
          <Select value="" onChange={(event) => { const video = videos.data?.find((v) => v.id === Number(event.target.value)); if (video) onPicked(video); }}>
            <option value="">Choose…</option>
            {videos.data?.map((video) => (
              <option key={video.id} value={video.id}>{video.title} · {duration(video.duration)} · {video.clip_count} clips</option>
            ))}
          </Select>
        </Field>
      )}
    </div>
  );
}

function SourceCard({ video, onChange }: { video: Video; onChange: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [creator, setCreator] = useState(video.creator_name);
  const [note, setNote] = useState(video.permission_note);
  useEffect(() => { setCreator(video.creator_name); setNote(video.permission_note); }, [video.id, video.creator_name, video.permission_note]);

  const save = async (body: Record<string, unknown>) => {
    try {
      const updated = await api.patch<Video>(`/videos/${video.id}`, body);
      client.setQueryData(["video", video.id], updated);
      for (const key of ["videos", "clips", "projects"]) client.invalidateQueries({ queryKey: [key] });
    } catch (e) {
      toast({ tone: "bad", title: "Could not save", body: (e as Error).message });
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-3">
        <div className="relative w-36 shrink-0 overflow-hidden rounded-lg border border-line bg-black" style={{ aspectRatio: `${video.width} / ${video.height}` }}>
          {video.thumb_url && <img src={video.thumb_url} alt="" className="h-full w-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-medium text-ink">{video.title}</p>
          <p className="mt-1 text-xs text-muted">{duration(video.duration)} · {video.width}×{video.height} · {Math.round(video.fps)} fps · {bytes(video.size_bytes)}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Badge title={video.storage_mode === "linked" ? "The file stays where you keep it" : "Stored in the app's data folder"}><HardDrive className="h-3 w-3" />{video.storage_mode === "linked" ? "Linked from disk" : "Uploaded"}</Badge>
            {!video.has_audio && <Badge tone="warn">No audio track</Badge>}
            {video.duplicate_of && <Badge tone="warn" title="The same file was added before">Duplicate of #{video.duplicate_of}</Badge>}
          </div>
          <button type="button" onClick={onChange} className="mt-2 text-xs text-accent-soft hover:underline">Use a different video</button>
        </div>
      </div>

      <div className="rounded-xl border border-line bg-bg/60 p-3">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wider text-faint">Source and rights</p>
          <RightsBadge status={video.permission_status} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Creator">
            <Input value={creator} onChange={(event) => setCreator(event.target.value)} onBlur={() => creator !== video.creator_name && save({ creator_name: creator })} placeholder="Who made it" />
          </Field>
          <Field label="Am I allowed to clip this?">
            <Select value={video.permission_status} onChange={(event) => save({ permission_status: event.target.value })}>
              <option value="unknown">Not recorded</option>
              <option value="owned">Yes: it is my own content</option>
              <option value="licensed">Yes: licence or campaign</option>
              <option value="permission">Yes: the creator said so</option>
              <option value="denied">No</option>
            </Select>
          </Field>
        </div>
        <Field label="Project" hint="Optional. Groups this video and its clips with others, for example by series or client." className="mt-2">
          <ProjectSelect value={video.project_id} onChange={(projectId) => save({ project_id: projectId })} />
        </Field>
        <Field label="Note (where the permission comes from)" className="mt-2">
          <Input value={note} onChange={(event) => setNote(event.target.value)} onBlur={() => note !== video.permission_note && save({ permission_note: note })} placeholder="e.g. campaign page, email from the creator" />
        </Field>
        {video.permission_status === "unknown" && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-warn"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />A video being public does not make it free to repost. Record your right to clip it before you publish.</p>
        )}
      </div>
    </div>
  );
}

function OptionsPanel({ value, onChange }: { value: ClipOptions; onChange: (next: ClipOptions) => void }) {
  const options = useOptions().data;
  const set = <K extends keyof ClipOptions>(key: K, v: ClipOptions[K]) => onChange({ ...value, [key]: v, workflow: key === "workflow" ? (v as string) : "custom" });
  const applyWorkflow = (key: string) => {
    const preset = options?.workflow_presets.find((p) => p.key === key);
    if (!preset?.config) return onChange({ ...value, workflow: "custom" });
    onChange({ ...value, ...preset.config, workflow: key });
  };
  const preset = options?.content_presets.find((p) => p.key === value.content_preset);
  const saved = usePresets();
  const toast = useToast();
  const [naming, setNaming] = useState<string | null>(null);  // the name being typed, while saving a preset
  const [saving, setSaving] = useState(false);
  const mine = saved.data?.find((p) => `saved-${p.id}` === value.workflow);
  const applySaved = (p: SavedPreset) => onChange({ ...value, ...p.config, workflow: `saved-${p.id}` });
  const savePreset = async () => {
    const name = (naming ?? "").trim();
    if (!name) return;
    setSaving(true);
    try {
      const { workflow: _workflow, ...config } = value;
      const made = await api.post<SavedPreset>("/presets", { name, config });
      await saved.refetch();
      onChange({ ...value, workflow: `saved-${made.id}` });
      setNaming(null);
      toast({ tone: "good", title: "Preset saved", body: `“${made.name}” is now under Start from a preset.` });
    } catch (error) {
      toast({ tone: "bad", title: "Could not save the preset", body: (error as Error).message });
    } finally {
      setSaving(false);
    }
  };
  const removePreset = async (p: SavedPreset) => {
    if (!window.confirm(`Remove your preset “${p.name}”? Clips already made with it stay as they are.`)) return;
    try {
      await api.del(`/presets/${p.id}`);
      await saved.refetch();
      if (value.workflow === `saved-${p.id}`) onChange({ ...value, workflow: "custom" });
    } catch (error) {
      toast({ tone: "bad", title: "Could not remove the preset", body: (error as Error).message });
    }
  };
  const chip = (active: boolean) => cx("rounded-full border text-[12.5px] transition-colors", active ? "border-accent/60 bg-accent/10 text-accent-soft" : "border-line text-muted hover:border-line-strong hover:text-ink");
  return (
    <div className="space-y-4">
      <FocusPicker focus={value.focus} look={value.look} onFocus={(key) => set("focus", key)} onLook={(on) => set("look", on)} />
      <Field label="Start from a preset" group hint={mine ? `Your preset “${mine.name}”.` : options?.workflow_presets.find((p) => p.key === value.workflow)?.description ?? "Your own settings."}>
        <div className="flex flex-wrap gap-1.5">
          {options?.workflow_presets.map((p) => (
            <button key={p.key} type="button" onClick={() => applyWorkflow(p.key)} className={cx(chip(value.workflow === p.key), "px-3 py-1")}>
              {p.label}
            </button>
          ))}
          {saved.data?.map((p) => (
            <span key={p.id} className={cx(chip(value.workflow === `saved-${p.id}`), "inline-flex items-center")}>
              <button type="button" onClick={() => applySaved(p)} className="inline-flex items-center gap-1 py-1 pl-2.5 pr-1" title="Your own preset">
                <Bookmark className="h-3 w-3" />{p.name}
              </button>
              <button type="button" onClick={() => removePreset(p)} className="mr-1 rounded-full p-0.5 text-faint hover:bg-hover hover:text-ink" title={`Remove your preset “${p.name}”`} aria-label={`Remove your preset ${p.name}`}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
        {naming === null ? (
          <button type="button" onClick={() => setNaming("")} className="mt-2 inline-flex items-center gap-1.5 text-xs text-accent-soft hover:underline">
            <BookmarkPlus className="h-3.5 w-3.5" />Save these settings as a preset
          </button>
        ) : (
          <div className="mt-2 flex items-center gap-2">
            <Input autoFocus value={naming} maxLength={60} placeholder="Name, e.g. My GTA streams" aria-label="Preset name"
              onChange={(event) => setNaming(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") savePreset(); if (event.key === "Escape") setNaming(null); }} />
            <Button size="sm" variant="primary" onClick={savePreset} busy={saving} disabled={!naming.trim()}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setNaming(null)} disabled={saving}>Cancel</Button>
          </div>
        )}
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Kind of content" hint={preset?.description}>
          <Select value={value.content_preset} onChange={(event) => set("content_preset", event.target.value)}>
            {options?.content_presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </Select>
        </Field>
        <Field label="Game" hint="Adds that game's vocabulary to what counts as relevant.">
          <Select value={value.game_style} onChange={(event) => set("game_style", event.target.value)} disabled={value.content_preset === "podcast"}>
            <option value="">Not a game / any</option>
            {options?.game_styles.map((g) => <option key={g.key} value={g.key}>{g.label}</option>)}
          </Select>
        </Field>
        <Field label="How many clips">
          <Input type="number" min={1} max={30} value={value.count} onChange={(event) => set("count", Math.min(30, Math.max(1, Number(event.target.value) || 1)))} />
        </Field>
        <Field label="Clip length">
          <Select value={value.length} onChange={(event) => set("length", event.target.value)}>
            {options?.length_ranges.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </Select>
        </Field>
      </div>

      <Field label={`Minimum score: ${value.min_score}`} hint="Fewer, stronger suggestions instead of padding. Set to 0 to see everything.">
        <input type="range" min={0} max={90} step={5} value={value.min_score} onChange={(event) => set("min_score", Number(event.target.value))} className="w-full" />
      </Field>

      {/* one row each: side by side they ran out of the card */}
      <Field label="Shape" group>
        <Segmented className="w-full [&>button]:flex-1" value={value.aspect} onChange={(v) => set("aspect", v)} options={(options?.aspects ?? []).map((a) => ({ value: a.key, label: a.key, title: `${a.width}×${a.height}` }))} />
      </Field>
      <Field label="Encode for" group>
        <Segmented className="w-full [&>button]:flex-1" value={value.quality} onChange={(v) => set("quality", v)} options={[{ value: "speed", label: "Speed" }, { value: "balanced", label: "Balanced" }, { value: "quality", label: "Quality" }]} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Layout" hint="Automatic looks for a face or facecam in each clip.">
          <Select value={value.layout} onChange={(event) => set("layout", event.target.value)}>
            {options?.layouts.map((l) => <option key={l.key} value={l.key} disabled={l.key === "split"}>{l.label}{l.key === "split" ? " (auto only)" : ""}</option>)}
          </Select>
        </Field>
        <Field label="Caption style">
          <Select value={value.captions} onChange={(event) => set("captions", event.target.value)}>
            {options?.caption_presets.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </Select>
        </Field>
      </div>

      <div className="space-y-3 rounded-xl border border-line bg-bg/60 p-3">
        <Switch checked={value.auto_render} onChange={(v) => set("auto_render", v)} label="Render the clips straight away" hint="Off: only suggest moments. You render the ones you want." />
        <Switch checked={value.show_hook} onChange={(v) => set("show_hook", v)} label="Hook title on screen" hint="A line from the clip, shown for the first 4 seconds." />
        <Switch checked={value.remove_silence} onChange={(v) => set("remove_silence", v)} label="Tighten long pauses" hint="Cuts silences over 0.8 s down to a short breath." />
      </div>
    </div>
  );
}

export default function Clipper() {
  const [params, setParams] = useSearchParams();
  const videoId = params.get("video") ? Number(params.get("video")) : null;
  const video = useVideo(videoId);
  const settings = useSettings();
  const options = useOptions();
  const jobs = useActiveJobs();
  const refresh = useRefreshAll();
  const toast = useToast();
  const actions = useClipActions();
  const [opts, setOpts] = useState<ClipOptions>(DEFAULTS);
  const [loaded, setLoaded] = useState(false);
  const [filter, setFilter] = useState<"all" | "pending" | "approved" | "rejected">("all");
  const [viewing, setViewing] = useState<number | null>(null);
  const [peek, setPeek] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<unknown>(null);
  const [moment, setMoment] = useState<MomentTimes>(emptyMoment);

  // Start from the defaults saved in Settings, once.
  useEffect(() => {
    const v = settings.data?.values;
    if (!v || loaded) return;
    setOpts((current) => ({
      ...current, content_preset: String(v["clips.content_preset"]), game_style: String(v["clips.game_style"]),
      focus: String(v["clips.focus"] ?? "best"), look: Boolean(v["clips.look"] ?? false),
      count: Number(v["clips.count"]), length: String(v["clips.length"]), min_score: Number(v["clips.min_score"]),
      aspect: String(v["clips.aspect"]), layout: String(v["clips.layout"]), captions: String(v["clips.captions"]),
      quality: String(v["processing.priority"]), auto_render: Boolean(v["clips.auto_render"]), show_hook: Boolean(v["clips.show_hook"]),
    }));
    setLoaded(true);
  }, [settings.data, loaded]);

  const myJobs = useMemo(() => (jobs.data ?? []).filter((job) => job.video_id === videoId), [jobs.data, videoId]);
  const analysis = myJobs.find((job) => job.type === "analyze") ?? null;
  const momentJobs = myJobs.filter((job) => job.type === "moment");
  const renderJobs = useMemo(() => new Map<number, Job>(myJobs.filter((j) => j.type === "render" && j.clip_id).map((j) => [j.clip_id as number, j])), [myJobs]);
  const clips = useClips({ video_id: videoId, sort: "rank" }, videoId !== null);

  // When a job starts, finishes or changes stage, reload what it affects.
  const signature = myJobs.map((job) => `${job.id}:${job.status}:${job.stage}`).join("|");
  const previous = useRef(signature);
  useEffect(() => {
    if (previous.current !== signature) {
      previous.current = signature;
      refresh();
    }
  }, [signature, refresh]);

  const pick = (v: Video | null) => {
    setFilter("all");
    setStartError(null);
    setMoment(emptyMoment);
    setParams(v ? { video: String(v.id) } : {});
  };

  const analyzeBody = () => {
    const { workflow: _workflow, ...body } = opts;
    return body;
  };
  const openImported = (id: number) => {
    setFilter("all");
    setStartError(null);
    refresh();
    setParams({ video: String(id) });
  };

  const generate = async () => {
    if (!video.data) return;
    setStarting(true);
    setStartError(null);
    try {
      await api.post(`/videos/${video.data.id}/analyze`, analyzeBody());
      refresh();
    } catch (e) {
      setStartError(e);
    } finally {
      setStarting(false);
    }
  };

  const all = clips.data?.items ?? [];
  const shown = filter === "all" ? all : all.filter((clip) => clip.review_status === filter);
  const counts = { all: all.length, pending: all.filter((c) => c.review_status === "pending").length, approved: all.filter((c) => c.review_status === "approved").length, rejected: all.filter((c) => c.review_status === "rejected").length };
  const viewed = all.find((clip) => clip.id === viewing) ?? null;
  const hasManualOrder = all.some((clip) => clip.manual_rank !== null);
  const unrendered = all.filter((clip) => clip.render_status === "none" && clip.review_status !== "rejected");

  const move = (clip: Clip, direction: -1 | 1) => {
    const index = all.findIndex((c) => c.id === clip.id);
    const target = index + direction;
    if (target < 0 || target >= all.length) return;
    const ids = all.map((c) => c.id);
    [ids[index], ids[target]] = [ids[target], ids[index]];
    actions.move(ids);
  };
  const resetOrder = async () => {
    await api.post(`/clips/reset-order?video_id=${videoId}`);
    refresh();
  };
  const renderAll = async () => {
    await api.post("/clips/batch", { ids: unrendered.map((c) => c.id), action: "render" });
    refresh();
    toast({ tone: "neutral", title: `${unrendered.length} renders queued` });
  };

  const blocked = video.data?.permission_status === "denied";
  const kind: Kind = {
    focus: opts.focus, look: opts.look,
    onFocus: (key) => setOpts((current) => ({ ...current, focus: key })),
    onLook: (on) => setOpts((current) => ({ ...current, look: on })),
  };
  const analysisNote = String((video.data?.analysis as { note?: string } | null)?.note || "");

  return (
    <div className="grid gap-6 xl:grid-cols-[410px_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card>
          <CardHeader title="1 · Source video" hint={video.data ? undefined : "Upload a recording, paste a link, or point at a file on this PC."} />
          <div className="px-5 pb-5">
            {videoId !== null && video.isError ? (
              <div className="space-y-3"><ErrorNote error={video.error} /><Button onClick={() => pick(null)}>Choose another video</Button></div>
            ) : video.data ? <SourceCard video={video.data} onChange={() => pick(null)} />
              : <SourcePicker onPicked={pick} onImported={openImported} analyzeBody={analyzeBody} kind={kind} />}
          </div>
        </Card>

        <Card>
          <CardHeader title="2 · How to clip it" hint={video.data ? undefined : "Set this up now if you like; it is used as soon as a video is here."} />
          <div className="space-y-4 px-5 pb-5">
            <OptionsPanel value={opts} onChange={setOpts} />
            <ErrorNote error={startError} />
            {blocked && <ErrorNote error={new Error("This source is marked as not permitted for clipping. Change that above if it is wrong.")} />}
            <Button variant="primary" size="lg" className="w-full justify-center" onClick={generate} busy={starting} disabled={!video.data || analysis !== null || blocked}>
              <Sparkles className="h-4 w-4" />{analysis ? "Working…" : all.length ? "Generate again" : "Generate Clips"}
            </Button>
            {all.length > 0 && !analysis && <p className="text-center text-xs text-faint">Suggestions you have not touched are replaced. Approved, rejected, edited and favourite clips are kept.</p>}
          </div>
        </Card>

        {video.data && (
          <MomentClip video={video.data} times={moment} onTimes={setMoment} analyzeBody={analyzeBody} jobs={momentJobs}
            onWatch={setPeek} claudeOn={settings.data?.values["ai.engine"] === "claude"} onQueued={refresh} />
        )}
      </div>

      <div className="min-w-0 space-y-4">
        {analysis && (
          <Card>
            <CardHeader title="Analysing" hint={video.data ? `${video.data.title} · ${duration(video.data.duration)}` : undefined} icon={<Wand2 className="h-4 w-4 text-accent" />} />
            <div className="px-5 pb-5"><JobStages job={analysis} /></div>
          </Card>
        )}
        {video.data?.status === "failed" && !analysis && (
          <Card><div className="space-y-3 p-5"><ErrorNote error={new Error(video.data.last_error || "The analysis failed.")} /><Button onClick={generate}>Try again</Button></div></Card>
        )}

        {!video.data && (
          <Empty icon={<Sparkles className="h-8 w-8" />} title="Your clips will appear here">
            Add a video on the left and press Generate Clips. ClipForge transcribes it, finds the moments that stand on their own, ranks them and renders vertical clips with captions.
          </Empty>
        )}

        {video.data && all.length === 0 && !analysis && video.data.status !== "failed" && (
          <Empty icon={<Sparkles className="h-8 w-8" />} title={video.data.status !== "analyzed" ? "Ready when you are" : analysisNote.includes("holds up as") ? "Nothing of that kind held up" : "No moment scored above your minimum"}>
            {video.data.status === "analyzed"
              ? analysisNote || "Lower the minimum score or widen the clip length, then generate again."
              : "Choose your options and press Generate Clips."}
          </Empty>
        )}

        {all.length > 0 && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Segmented value={filter} onChange={setFilter} options={[
                { value: "all", label: `All ${counts.all}` }, { value: "pending", label: `To review ${counts.pending}` },
                { value: "approved", label: `Approved ${counts.approved}` }, { value: "rejected", label: `Rejected ${counts.rejected}` },
              ]} />
              <span className="flex-1" />
              {unrendered.length > 0 && <Button size="sm" onClick={renderAll}>Render {unrendered.length} unrendered</Button>}
              {hasManualOrder && <Button size="sm" variant="ghost" onClick={resetOrder} title="Drop your own order and sort by score again"><RotateCcw className="h-3.5 w-3.5" />Back to AI ranking</Button>}
            </div>
            <p className="text-xs text-faint">
              Ranked by estimated engagement potential. These scores are heuristic estimates, not predictions of views. Use the arrows to set your own order.
              {video.data && ` Source: ${video.data.title} (${clock(video.data.duration)}).`}
            </p>
            <div className="space-y-3">
              {shown.map((clip) => (
                <ClipCard key={clip.id} clip={clip} position={all.indexOf(clip) + 1} options={options.data} job={renderJobs.get(clip.id)}
                  onOpen={() => setViewing(clip.id)} onPeek={setPeek} onMove={(direction) => move(clip, direction)} />
              ))}
              {shown.length === 0 && <Empty title="Nothing in this filter" />}
            </div>
          </>
        )}
      </div>

      <ClipViewer clip={viewed} options={options.data} onClose={() => setViewing(null)} onPeek={setPeek} />
      {videoId !== null && <div className="xl:col-start-2"><VideoWorkLog videoId={videoId} /></div>}
      <SourcePeek videoId={videoId} time={peek} title={video.data?.title} onClose={() => setPeek(null)} marks={moment}
        onMarks={setMoment} onMark={(which, seconds) => setMoment((current) => markMoment(current, which, seconds))} />
    </div>
  );
}
