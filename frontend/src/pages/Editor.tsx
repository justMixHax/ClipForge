// The clip editor: trim on a waveform, edit every caption word, restyle, reframe, then render.
// Changes are kept in a draft with undo and redo, and only reach the clip when you save or render.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Download, Pause, Play, Redo2, RotateCcw, Save, Undo2, Wand2, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Link, useParams } from "react-router-dom";
import { ClipThumb, ScoreRing } from "../components/clips";
import { JobStages } from "../components/JobStages";
import { ThumbnailPanel, TikTokCaption } from "../components/thumbnail";
import { Timeline, type TimedWord } from "../components/Timeline";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, NumberField, Segmented, Select, Spinner, Switch, Textarea, useToast } from "../components/ui";
import { api, query, type CaptionPreset, type CaptionSettings, type CaptionWord, type Clip, type ClipSettings, type Options, type Segment, type Timeline as TimelineData, type Video } from "../lib/api";
import { clock, cx, duration } from "../lib/format";
import { useActiveJobs, useClip, useClips, useOptions, useRefreshAll, useVideo } from "../lib/hooks";

type Draft = {
  start: number;
  end: number;
  title: string;
  hook_text: string;
  description: string;
  hashtags: string;
  settings: ClipSettings;
  captions: CaptionWord[] | null;
};

const toDraft = (clip: Clip): Draft => ({
  start: clip.start, end: clip.end, title: clip.title, hook_text: clip.hook_text, description: clip.description,
  hashtags: clip.hashtags.join(" "), settings: structuredClone(clip.settings), captions: clip.captions ? structuredClone(clip.captions) : null,
});

export function EditorIndex() {
  const clips = useClips({ sort: "newest" });
  if (clips.isLoading) return <div className="flex justify-center py-24"><Spinner className="h-6 w-6" /></div>;
  const items = clips.data?.items ?? [];
  if (items.length === 0) return <Empty title="No clips to edit yet">Generate clips in the <Link to="/clipper" className="text-accent-soft hover:underline">AI Clipper</Link>, or search inside a video and make one from the result.</Empty>;
  return (
    <div className="space-y-4">
      <p className="text-[13px] text-muted">Pick a clip to open it in the editor.</p>
      <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
        {items.map((clip) => (
          <li key={clip.id}>
            <Link to={`/editor/${clip.id}`} className="block rounded-2xl border border-line bg-panel p-2.5 transition-colors hover:border-line-strong">
              <div className="pointer-events-none"><ClipThumb clip={clip} className="w-full" /></div>
              <p className="mt-2 line-clamp-2 text-[13px] text-ink">{clip.title}</p>
              <p className="text-xs text-faint">{clock(clip.start)} · {duration(clip.end - clip.start)}</p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- preview
function OutputPreview({ videoRef, video, draft, words, time, layout, cropX, preset, onPan, onPanStart }: {
  videoRef: RefObject<HTMLVideoElement | null>; video: Video; draft: Draft; words: CaptionWord[]; time: number;
  layout: string; cropX: number; preset?: CaptionPreset; onPan: (x: number) => void; onPanStart: () => void;
}) {
  const [aw, ah] = draft.settings.aspect.split(":").map(Number);
  const captions: CaptionSettings = draft.settings.captions ?? {};
  const base = preset?.style;
  const t = time - draft.start;
  const perLine = captions.max_words ?? base?.max_words ?? 3;
  const visible = words.filter((word) => !word.hidden);
  const active = visible.findIndex((word) => t >= word.start && t < word.end + 0.15);
  const lineStart = active >= 0 ? Math.floor(active / perLine) * perLine : -1;
  const line = lineStart >= 0 ? visible.slice(lineStart, lineStart + perLine) : [];
  const upper = captions.uppercase ?? base?.uppercase ?? true;
  const fill = layout === "fill";
  const dragging = useRef(false);
  const hookOn = draft.settings.hook?.enabled !== false && draft.hook_text && t >= 0 && t < (draft.settings.hook?.seconds ?? 4);

  const pan = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current || !fill) return;
    const rect = event.currentTarget.getBoundingClientRect();
    onPan(Math.min(1, Math.max(0, 1 - (event.clientX - rect.left) / rect.width)));
  };

  return (
    <div className="mx-auto w-full" style={{ maxWidth: aw >= ah ? 560 : 300 }}>
      <div className={cx("relative overflow-hidden rounded-2xl border border-line bg-[#07080a]", fill && "cursor-grab active:cursor-grabbing")} style={{ aspectRatio: `${aw} / ${ah}` }}
        onPointerDown={(event) => { if (fill) { dragging.current = true; onPanStart(); event.currentTarget.setPointerCapture(event.pointerId); } }}
        onPointerMove={pan} onPointerUp={() => { dragging.current = false; }}>
        <video ref={videoRef} src={video.stream_url} playsInline preload="auto" className="absolute inset-0 h-full w-full"
          style={fill ? { objectFit: "cover", objectPosition: `${cropX * 100}% 50%`, transform: `scale(${draft.settings.zoom || 1})`, transformOrigin: `${cropX * 100}% 50%` } : { objectFit: "contain" }} />
        {hookOn && (
          <div className="pointer-events-none absolute inset-x-0 flex justify-center px-3" style={{ top: fill ? "13%" : "22%" }}>
            <span className="rounded bg-white px-2 py-1 text-center text-[11px] font-extrabold uppercase leading-tight text-black">{draft.hook_text}</span>
          </div>
        )}
        {captions.enabled !== false && line.length > 0 && (
          <div className="pointer-events-none absolute inset-x-0 flex justify-center px-2" style={{ top: `${(captions.position ?? (fill ? base?.position ?? 0.7 : 0.73)) * 100}%`, transform: "translateY(-50%)" }}>
            <p className="text-center font-extrabold leading-tight" style={{ fontFamily: `"${captions.font ?? base?.font ?? "Arial Black"}", sans-serif`, fontSize: `${((captions.size ?? base?.size ?? 92) / 1080) * 100 * 0.36}cqw`, color: captions.color ?? base?.color ?? "#fff", textShadow: "0 0 4px #000, 0 2px 0 #000, 2px 0 0 #000, -2px 0 0 #000, 0 -2px 0 #000" }}>
              {line.map((word, index) => (
                <span key={`${word.start}-${index}`} style={{ color: lineStart + index === active ? captions.highlight ?? base?.highlight : word.emph ? captions.emphasis ?? base?.emphasis ?? undefined : undefined }}>
                  {upper ? word.text.toUpperCase() : word.text}{" "}
                </span>
              ))}
            </p>
          </div>
        )}
      </div>
      <p className="mt-2 text-center text-xs text-faint">
        {fill ? "Live preview. Drag sideways to choose what stays in frame." : layout === "fit_blur" ? "Live preview. The render adds a blurred copy of the picture behind it." : "Live preview of the whole frame. Render to see the stacked layout."}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- panels
function CaptionsPanel({ draft, words, edited, time, onSeek, onEdit, onReset }: {
  draft: Draft; words: CaptionWord[]; edited: boolean; time: number; onSeek: (t: number) => void;
  onEdit: (index: number, patch: Partial<CaptionWord>) => void; onReset: () => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const t = time - draft.start;
  const word = selected !== null ? words[selected] : null;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-faint">{words.length} words. Click one to hear it and edit it.</p>
        {edited && <Button size="sm" variant="ghost" onClick={() => { setSelected(null); onReset(); }}><RotateCcw className="h-3.5 w-3.5" />Back to the transcript</Button>}
      </div>
      {words.length === 0 && <p className="text-[13px] text-muted">No speech was found in this range.</p>}
      <div className="flex max-h-64 flex-wrap gap-1 overflow-y-auto rounded-xl border border-line bg-bg/60 p-2.5">
        {words.map((w, index) => (
          <button key={`${w.start}-${index}`} type="button" onClick={() => { setSelected(index); onSeek(draft.start + w.start + 0.01); }}
            className={cx("rounded-md px-1.5 py-0.5 text-[13px] transition-colors", selected === index ? "bg-accent text-[#1a0d05]" : t >= w.start && t < w.end ? "bg-raised text-ink ring-1 ring-accent/60" : "text-muted hover:bg-hover hover:text-ink",
              w.hidden && "line-through opacity-50", w.emph && selected !== index && "font-semibold text-warn")}>
            {w.text}
          </button>
        ))}
      </div>
      {word && selected !== null && (
        <div className="space-y-3 rounded-xl border border-line bg-bg/60 p-3 animate-fade">
          <Field label="Text shown on screen" hint="Changes the caption only, never the audio.">
            <Input value={word.text} maxLength={80} onChange={(event) => onEdit(selected, { text: event.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Appears at (s)"><NumberField step={0.05} min={0} value={word.start} onCommit={(value) => value < word.end && onEdit(selected, { start: Math.max(0, value) })} /></Field>
            <Field label="Ends at (s)"><NumberField step={0.05} min={0} value={word.end} onCommit={(value) => value > word.start && onEdit(selected, { end: value })} /></Field>
          </div>
          <Switch checked={Boolean(word.emph)} onChange={(value) => onEdit(selected, { emph: value })} label="Emphasise this word" hint="Drawn in the emphasis colour." />
          <Switch checked={Boolean(word.hidden)} onChange={(value) => onEdit(selected, { hidden: value })} label="Hide this word" hint="Stays in the transcript, not on screen." />
        </div>
      )}
      {!edited && <p className="text-xs text-faint">Important words (numbers, strong words) are emphasised automatically. Once you edit a word, the captions are yours and are rendered exactly as shown.</p>}
    </div>
  );
}

function StylePanel({ draft, options, preset, setCaptions, setSettings, setField }: {
  draft: Draft; options: Options; preset?: CaptionPreset; setCaptions: (patch: CaptionSettings) => void;
  setSettings: (patch: Partial<ClipSettings>) => void; setField: (patch: Partial<Draft>) => void;
}) {
  const c = draft.settings.captions ?? {};
  const base = preset?.style;
  const hook = draft.settings.hook ?? {};
  return (
    <div className="space-y-4">
      <Switch checked={c.enabled !== false} onChange={(value) => setCaptions({ enabled: value })} label="Burn captions into the video" />
      <Field label="Style">
        <div className="grid grid-cols-3 gap-1.5">
          {options.caption_presets.map((p) => (
            <button key={p.key} type="button" onClick={() => setSettings({ captions: { enabled: c.enabled, preset: p.key, censor: c.censor } })}
              className={cx("rounded-lg border px-2 py-2 text-center transition-colors", (c.preset ?? "opus") === p.key ? "border-accent/60 bg-accent/10" : "border-line hover:border-line-strong")}>
              <span className="block truncate text-[13px] font-extrabold" style={{ fontFamily: `"${p.style.font}", sans-serif`, color: p.style.highlight, textTransform: p.style.uppercase ? "uppercase" : "none" }}>Aa</span>
              <span className="block truncate text-[11px] text-muted">{p.label}</span>
            </button>
          ))}
        </div>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Font">
          <Select value={c.font ?? base?.font ?? ""} onChange={(event) => setCaptions({ font: event.target.value })}>
            {options.fonts.map((font) => <option key={font.name} value={font.name}>{font.name}</option>)}
          </Select>
        </Field>
        <Field label={`Words per line: ${c.max_words ?? base?.max_words ?? 3}`}>
          <input type="range" min={1} max={8} value={c.max_words ?? base?.max_words ?? 3} onChange={(event) => setCaptions({ max_words: Number(event.target.value) })} className="w-full" />
        </Field>
        <Field label={`Size: ${c.size ?? base?.size ?? 92}`}>
          <input type="range" min={40} max={160} step={2} value={c.size ?? base?.size ?? 92} onChange={(event) => setCaptions({ size: Number(event.target.value) })} className="w-full" />
        </Field>
        <Field label={`Height on screen: ${Math.round((c.position ?? base?.position ?? 0.7) * 100)}%`} hint="Kept inside the safe area.">
          <input type="range" min={0.1} max={0.8} step={0.01} value={c.position ?? base?.position ?? 0.7} onChange={(event) => setCaptions({ position: Number(event.target.value) })} className="w-full" />
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-3">
        {([["color", "Text"], ["highlight", "Spoken word"], ["emphasis", "Emphasis"]] as const).map(([key, label]) => (
          <Field key={key} label={label}>
            <input type="color" className="h-9 w-full cursor-pointer rounded-lg border border-line" value={(c[key] as string | undefined) ?? (base?.[key] as string | null) ?? "#ffffff"} onChange={(event) => setCaptions({ [key]: event.target.value.toUpperCase() })} />
          </Field>
        ))}
      </div>
      <Switch checked={c.uppercase ?? base?.uppercase ?? true} onChange={(value) => setCaptions({ uppercase: value })} label="All capitals" />
      <Switch checked={c.censor !== false} onChange={(value) => setCaptions({ censor: value })} label="Mask swear words on screen" hint="The SRT and VTT files stay word for word." />

      <div className="space-y-3 border-t border-line pt-4">
        <Switch checked={hook.enabled !== false} onChange={(value) => setSettings({ hook: { ...hook, enabled: value } })} label="Hook title at the start" />
        <Field label="Hook text" hint="Suggested lines are direct quotes from the clip. If you write your own, keep it true to what happens.">
          <Input value={draft.hook_text} maxLength={300} onChange={(event) => setField({ hook_text: event.target.value })} />
        </Field>
        <HookOptions draft={draft} setField={setField} />
        <Field label={`Shown for ${hook.seconds ?? 4} s`}>
          <input type="range" min={1} max={15} step={0.5} value={hook.seconds ?? 4} onChange={(event) => setSettings({ hook: { ...hook, seconds: Number(event.target.value) } })} className="w-full" />
        </Field>
      </div>
    </div>
  );
}

function HookOptions({ draft, setField }: { draft: Draft; setField: (patch: Partial<Draft>) => void }) {
  const { clipId } = useParams();
  const clip = useClip(Number(clipId));
  const hooks = clip.data?.hook_options ?? [];
  if (hooks.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {hooks.map((hook) => (
        <button key={hook} type="button" onClick={() => setField({ hook_text: hook })}
          className={cx("rounded-full border px-2.5 py-1 text-left text-xs transition-colors", draft.hook_text === hook ? "border-accent/60 bg-accent/10 text-accent-soft" : "border-line text-muted hover:border-line-strong hover:text-ink")}>
          {hook}
        </button>
      ))}
    </div>
  );
}

function LayoutPanel({ draft, options, reason, setSettings }: { draft: Draft; options: Options; reason?: string; setSettings: (patch: Partial<ClipSettings>) => void }) {
  const s = draft.settings;
  const box = s.facecam_box ?? [0, 0, 0, 0];
  const setBox = (index: number, value: number) => {
    const next = [...box];
    next[index] = Math.max(0, value);
    setSettings({ facecam_box: next[2] > 0 && next[3] > 0 ? next : null });
  };
  return (
    <div className="space-y-4">
      <Field label="Shape">
        <Segmented value={s.aspect} onChange={(value) => setSettings({ aspect: value })} options={options.aspects.map((a) => ({ value: a.key, label: a.key, title: `${a.width}×${a.height}` }))} />
      </Field>
      <Field label="Layout" hint={reason ? `Last render: ${reason}` : "Automatic looks for a face or a facecam in the clip."}>
        <Select value={s.layout} onChange={(event) => setSettings({ layout: event.target.value })}>
          {options.layouts.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
        </Select>
      </Field>
      {(s.layout === "fill" || s.layout === "auto") && (
        <>
          <Switch checked={s.subject_x === null} onChange={(value) => setSettings({ subject_x: value ? null : 0.5 })} label="Find the subject automatically" hint="Off: you choose what stays in frame (drag the preview)." />
          {s.subject_x !== null && (
            <Field label={`Subject position: ${Math.round((s.subject_x ?? 0.5) * 100)}% from the left`}>
              <input type="range" min={0} max={1} step={0.01} value={s.subject_x ?? 0.5} onChange={(event) => setSettings({ subject_x: Number(event.target.value), layout: "fill" })} className="w-full" />
            </Field>
          )}
          <Field label={`Zoom: ${(s.zoom || 1).toFixed(2)}×`}>
            <input type="range" min={1} max={2.5} step={0.05} value={s.zoom || 1} onChange={(event) => setSettings({ zoom: Number(event.target.value) })} className="w-full" />
          </Field>
        </>
      )}
      {(s.layout === "facecam_top" || s.layout === "auto") && (
        <Field label="Facecam box in the source (x, y, width, height in pixels)" hint="Leave at 0 to let ClipForge find the facecam. Set it when the detection picks the wrong area.">
          <div className="grid grid-cols-4 gap-2">
            {["x", "y", "w", "h"].map((label, index) => <Input key={label} type="number" min={0} aria-label={label} placeholder={label} value={box[index] || ""} onChange={(event) => setBox(index, Number(event.target.value))} />)}
          </div>
        </Field>
      )}
      <div className="grid grid-cols-2 gap-3 border-t border-line pt-4">
        <Field label="Encode for">
          <Select value={s.quality} onChange={(event) => setSettings({ quality: event.target.value })}>
            <option value="speed">Speed</option><option value="balanced">Balanced</option><option value="quality">Quality</option>
          </Select>
        </Field>
        <Field label="Frames per second">
          <Select value={s.fps} onChange={(event) => setSettings({ fps: Number(event.target.value) })}>
            {[24, 25, 30, 50, 60].map((fps) => <option key={fps} value={fps}>{fps}</option>)}
          </Select>
        </Field>
      </div>
      <Switch checked={s.audio?.normalize !== false} onChange={(value) => setSettings({ audio: { ...s.audio, normalize: value } })} label="Level out the volume" />
      <Switch checked={Boolean(s.audio?.denoise)} onChange={(value) => setSettings({ audio: { ...s.audio, denoise: value } })} label="Reduce background noise" hint="A light filter. Leave off for game audio you want to keep." />
      <Switch checked={Boolean(s.remove_silence)} onChange={(value) => setSettings({ remove_silence: value })} label="Tighten long pauses" hint="Cuts silences down to a short breath. The live preview does not show these cuts; the render does." />
    </div>
  );
}

function DetailsPanel({ draft, clip, setField }: { draft: Draft; clip: Clip; setField: (patch: Partial<Draft>) => void }) {
  return (
    <div className="space-y-4">
      <Field label="Title"><Input value={draft.title} maxLength={300} onChange={(event) => setField({ title: event.target.value })} /></Field>
      <Field label="Description"><Textarea rows={3} value={draft.description} onChange={(event) => setField({ description: event.target.value })} /></Field>
      <Field label="Hashtags" hint="Separated by spaces."><Input value={draft.hashtags} onChange={(event) => setField({ hashtags: event.target.value })} /></Field>
      <div className="rounded-xl border border-line bg-bg/60 p-3 text-[13px]">
        <p className="text-muted">From <span className="text-ink">{clip.video_title}</span>{clip.creator_name && ` by ${clip.creator_name}`}</p>
        <p className="mt-1 text-muted">Source time {clock(draft.start, true)} – {clock(draft.end, true)}</p>
        {clip.analysis_source !== "manual" && <p className="mt-2 text-xs text-faint">{clip.explanation}</p>}
      </div>
      {clip.qc && (
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Checks on the last render</p>
          <ul className="space-y-1">
            {clip.qc.map((check) => (
              <li key={check.key} className="flex items-start gap-2 text-[13px]">
                {check.status === "pass" ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" /> : check.status === "warn" ? <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-bad" />}
                <span><span className="text-ink">{check.label}:</span> <span className="text-muted">{check.detail}</span></span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------------------------------- editor
export default function Editor() {
  const { clipId } = useParams();
  const id = Number(clipId);
  const clip = useClip(Number.isFinite(id) ? id : null);
  const video = useVideo(clip.data?.video_id ?? null);
  const options = useOptions();
  const jobs = useActiveJobs();
  const client = useQueryClient();
  const refresh = useRefreshAll();
  const toast = useToast();

  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState("");
  const history = useRef<{ past: Draft[]; future: Draft[] }>({ past: [], future: [] });
  const [, bump] = useState(0);
  const [tab, setTab] = useState<"captions" | "style" | "layout" | "thumbnail" | "details">("captions");
  const [view, setView] = useState<"edit" | "result">("edit");
  const [snap, setSnap] = useState(true);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "render">("");
  const [error, setError] = useState<unknown>(null);
  const [windowRange, setWindowRange] = useState<{ from: number; to: number } | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // Load the draft when another clip is opened (not on every refresh, so unsaved edits survive a re-render job).
  const loadedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!clip.data || loadedFor.current === clip.data.id) return;
    loadedFor.current = clip.data.id;
    const next = toDraft(clip.data);
    setDraft(next);
    setSaved(JSON.stringify(next));
    history.current = { past: [], future: [] };
    setTime(clip.data.start);
    setView(clip.data.render_status === "ready" ? "result" : "edit");
  }, [clip.data]);

  useEffect(() => {
    if (!clip.data || !video.data || windowRange) return;
    setWindowRange({ from: Math.max(0, clip.data.start - 20), to: Math.min(video.data.duration, clip.data.end + 20) });
  }, [clip.data, video.data, windowRange]);
  useEffect(() => { setWindowRange(null); }, [id]);

  const renderJob = (jobs.data ?? []).find((job) => job.clip_id === id && job.type === "render") ?? null;
  const wasRendering = useRef(false);
  useEffect(() => {
    if (wasRendering.current && !renderJob) {
      refresh();
      setView("result");
    }
    wasRendering.current = Boolean(renderJob);
  }, [renderJob, refresh]);

  const timeline = useQuery({
    queryKey: ["timeline", video.data?.id, windowRange?.from, windowRange?.to],
    queryFn: () => api.get<TimelineData>(`/videos/${video.data!.id}/timeline${query({ start: windowRange!.from, end: windowRange!.to, points: 900 })}`),
    enabled: Boolean(video.data && windowRange),
  });
  const transcript = useQuery({
    queryKey: ["transcript", video.data?.id, windowRange?.from, windowRange?.to],
    queryFn: () => api.get<Segment[]>(`/videos/${video.data!.id}/transcript${query({ start: windowRange!.from, end: windowRange!.to })}`),
    enabled: Boolean(video.data && windowRange),
  });
  const serverWords = useQuery({
    queryKey: ["captions", id, clip.data?.updated_at],
    queryFn: () => api.get<{ edited: boolean; words: CaptionWord[] }>(`/clips/${id}/captions`),
    enabled: Boolean(clip.data),
  });

  const sourceWords: TimedWord[] = useMemo(() => (transcript.data ?? []).flatMap((segment) => segment.words.map(([text, start, end]) => ({ text, start, end }))), [transcript.data]);

  // The caption words the render will use, in clip time.
  const words: CaptionWord[] = useMemo(() => {
    if (!draft) return [];
    if (draft.captions) return draft.captions;
    const untouched = clip.data && draft.start === clip.data.start && draft.end === clip.data.end && !draft.settings.remove_silence;
    if (untouched && serverWords.data && !serverWords.data.edited) return serverWords.data.words;
    return sourceWords.filter((w) => (w.start + w.end) / 2 >= draft.start && (w.start + w.end) / 2 < draft.end)
      .map((w) => ({ text: w.text, start: Math.max(0, Number((w.start - draft.start).toFixed(3))), end: Number((w.end - draft.start).toFixed(3)) }));
  }, [draft, clip.data, serverWords.data, sourceWords]);

  // ---- draft changes with undo/redo ------------------------------------------------------------
  const remember = useCallback(() => {
    if (!draft) return;
    history.current.past = [...history.current.past.slice(-99), structuredClone(draft)];
    history.current.future = [];
    bump((n) => n + 1);
  }, [draft]);
  const change = useCallback((next: Draft, record = true) => {
    if (record) remember();
    setDraft(next);
  }, [remember]);
  const undo = useCallback(() => {
    const { past, future } = history.current;
    if (!draft || past.length === 0) return;
    history.current = { past: past.slice(0, -1), future: [structuredClone(draft), ...future] };
    setDraft(past[past.length - 1]);
  }, [draft]);
  const redo = useCallback(() => {
    const { past, future } = history.current;
    if (!draft || future.length === 0) return;
    history.current = { past: [...past, structuredClone(draft)], future: future.slice(1) };
    setDraft(future[0]);
  }, [draft]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); }
      if (event.key === " ") { event.preventDefault(); toggle(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const setField = (patch: Partial<Draft>) => draft && change({ ...draft, ...patch });
  const setSettings = (patch: Partial<ClipSettings>, record = true) => draft && change({ ...draft, settings: { ...draft.settings, ...patch } }, record);
  const setCaptions = (patch: CaptionSettings) => draft && change({ ...draft, settings: { ...draft.settings, captions: { ...draft.settings.captions, ...patch } } });

  const setRange = (start: number, end: number, record: boolean) => {
    if (!draft || !video.data) return;
    start = Number(Math.max(0, start).toFixed(3));
    end = Number(Math.min(video.data.duration, end).toFixed(3));
    if (end - start < 0.5) return;
    const shift = draft.start - start;
    const captions = draft.captions
      ? draft.captions.map((w) => ({ ...w, start: Number((w.start + shift).toFixed(3)), end: Number((w.end + shift).toFixed(3)) })).filter((w) => w.end > 0.02 && w.start < end - start - 0.02).map((w) => ({ ...w, start: Math.max(0, w.start), end: Math.min(end - start, w.end) }))
      : null;
    change({ ...draft, start, end, captions }, record);
  };
  const editWord = (index: number, patch: Partial<CaptionWord>) => {
    if (!draft) return;
    const base = draft.captions ?? words.map((w) => ({ ...w, locked: true }));
    change({ ...draft, captions: base.map((w, i) => (i === index ? { ...w, ...patch, locked: true } : w)) });
  };

  // ---- playback ----------------------------------------------------------------------------------
  const seek = (t: number) => {
    if (videoRef.current) videoRef.current.currentTime = t;
    setTime(t);
  };
  const toggle = () => {
    const element = videoRef.current;
    if (!element || !draft || view !== "edit") return;
    if (element.paused) {
      if (element.currentTime < draft.start - 0.05 || element.currentTime >= draft.end - 0.05) element.currentTime = draft.start;
      element.play().catch(() => undefined);
    } else element.pause();
  };
  // The current in and out points, readable from event handlers without re-subscribing on every edit.
  const range = useRef({ start: 0, end: 0 });
  if (draft) range.current = { start: draft.start, end: draft.end };
  const mounted = Boolean(draft && video.data && options.data && windowRange);
  useEffect(() => {
    const element = videoRef.current;
    if (!element) return;
    let frame = 0;
    const tick = () => {
      setTime(element.currentTime);
      if (!element.paused && element.currentTime >= range.current.end) element.pause();
      frame = requestAnimationFrame(tick);
    };
    // Keep the picture inside the clip: on load, and whenever it is outside the current range.
    const place = () => {
      const { start, end } = range.current;
      if (element.currentTime < start - 0.05 || element.currentTime > end + 0.05) element.currentTime = start;
    };
    const onPlay = () => { setPlaying(true); frame = requestAnimationFrame(tick); };
    const onPause = () => { setPlaying(false); cancelAnimationFrame(frame); setTime(element.currentTime); };
    const onSeeked = () => setTime(element.currentTime);
    element.addEventListener("play", onPlay);
    element.addEventListener("pause", onPause);
    element.addEventListener("seeked", onSeeked);
    element.addEventListener("loadedmetadata", place);
    if (element.readyState >= 1) place();
    return () => {
      cancelAnimationFrame(frame);
      element.removeEventListener("play", onPlay);
      element.removeEventListener("pause", onPause);
      element.removeEventListener("seeked", onSeeked);
      element.removeEventListener("loadedmetadata", place);
    };
  }, [mounted]);
  // While trimming (paused), show the frame at the handle being moved: the new first or last frame.
  const previousRange = useRef({ start: 0, end: 0 });
  useEffect(() => {
    const element = videoRef.current;
    if (!element || !draft || !element.paused || element.readyState < 1) return;
    const before = previousRange.current;
    if (before.end !== 0 && before.start !== draft.start) element.currentTime = draft.start;
    else if (before.end !== 0 && before.end !== draft.end) element.currentTime = Math.max(draft.start, draft.end - 0.05);
    previousRange.current = { start: draft.start, end: draft.end };
  }, [draft?.start, draft?.end, draft]);

  // ---- save and render ---------------------------------------------------------------------------
  const dirty = draft !== null && JSON.stringify(draft) !== saved;
  const save = async (): Promise<boolean> => {
    if (!draft) return false;
    setError(null);
    try {
      const body = { start: draft.start, end: draft.end, title: draft.title, hook_text: draft.hook_text, description: draft.description,
        hashtags: draft.hashtags.split(/[\s,]+/).filter(Boolean), settings: draft.settings, captions: draft.captions };
      const updated = await api.patch<Clip>(`/clips/${id}`, body);
      client.setQueryData(["clip", id], updated);
      client.invalidateQueries({ queryKey: ["clips"] });
      const next = toDraft(updated);
      setDraft(next);
      setSaved(JSON.stringify(next));
      return true;
    } catch (e) {
      setError(e);
      return false;
    }
  };
  const onSave = async () => { setBusy("save"); if (await save()) toast({ tone: "good", title: "Saved" }); setBusy(""); };
  const onRender = async () => {
    setBusy("render");
    if (!dirty || (await save())) {
      try {
        await api.post(`/clips/${id}/render`);
        refresh();
        setView("result");
      } catch (e) {
        setError(e);
      }
    }
    setBusy("");
  };

  if (clip.isError) return <ErrorNote error={clip.error} />;
  if (!clip.data || !video.data || !options.data || !draft || !windowRange) return <div className="flex justify-center py-24"><Spinner className="h-6 w-6" /></div>;
  const c = clip.data;
  const plan = (c.render_info as { plan?: { layout?: string; crop?: number[]; reason?: string } } | null)?.plan;
  const layout = draft.settings.layout === "auto" ? plan?.layout ?? "fit_blur" : draft.settings.layout;
  const cropX = draft.settings.subject_x ?? (plan?.crop && video.data.width > plan.crop[2] ? plan.crop[0] / (video.data.width - plan.crop[2]) : 0.5);
  const preset = options.data.caption_presets.find((p) => p.key === (draft.settings.captions?.preset ?? "opus"));
  const length = draft.end - draft.start;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/clipper?video=${c.video_id}`}><Button variant="ghost" size="sm"><ArrowLeft className="h-4 w-4" />Back</Button></Link>
        <ScoreRing score={c.score} size={36} manual={c.analysis_source === "manual"} />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{draft.title || "Untitled clip"}</h2>
        {dirty ? <Badge tone="warn">Unsaved changes</Badge> : <Badge tone="good">Saved</Badge>}
        <Button size="icon" variant="ghost" onClick={undo} disabled={history.current.past.length === 0} title="Undo (Ctrl+Z)" aria-label="Undo"><Undo2 className="h-4 w-4" /></Button>
        <Button size="icon" variant="ghost" onClick={redo} disabled={history.current.future.length === 0} title="Redo (Ctrl+Y)" aria-label="Redo"><Redo2 className="h-4 w-4" /></Button>
        <Button onClick={onSave} busy={busy === "save"} disabled={!dirty}><Save className="h-4 w-4" />Save</Button>
        <Button variant="primary" onClick={onRender} busy={busy === "render"} disabled={Boolean(renderJob)}><Wand2 className="h-4 w-4" />{renderJob ? "Rendering…" : dirty ? "Save and render" : "Render"}</Button>
        {c.render_status === "ready" && <a href={`/api/clips/${c.id}/file?download=1`}><Button><Download className="h-4 w-4" />Download</Button></a>}
      </div>
      <ErrorNote error={error} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="min-w-0 space-y-4">
          <Card className="p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <Segmented value={view} onChange={setView} options={[{ value: "edit", label: "Live preview" }, { value: "result", label: "Last render" }]} />
              {view === "edit" && (
                <div className="flex items-center gap-2">
                  <Button size="sm" onClick={toggle}>{playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}{playing ? "Pause" : "Play clip"}</Button>
                  <span className="tabular text-[13px] text-muted">{clock(Math.max(0, time - draft.start), true)} / {clock(length, true)}</span>
                </div>
              )}
            </div>
            <div className={view === "edit" ? "" : "hidden"} style={{ containerType: "inline-size" }}>
              <OutputPreview videoRef={videoRef} video={video.data} draft={draft} words={words} time={time} layout={layout} cropX={cropX} preset={preset}
                onPanStart={remember} onPan={(x) => setSettings({ subject_x: Number(x.toFixed(3)), layout: "fill" }, false)} />
            </div>
            {view === "result" && (
              renderJob ? <div className="mx-auto max-w-md py-10"><JobStages job={renderJob} /></div>
                : c.render_status === "queued" || c.render_status === "rendering" ? <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-muted"><Spinner />Waiting for the render to start…</div>
                : c.render_status === "ready" ? (
                  <div className="mx-auto" style={{ maxWidth: c.out_width >= c.out_height ? 560 : 300 }}>
                    <video key={c.file_url} src={c.file_url} poster={c.thumb_url} controls playsInline className="w-full rounded-2xl border border-line" />
                    <p className="mt-2 text-center text-xs text-faint">{c.out_width}×{c.out_height} · {duration(c.out_duration)}{dirty && " · your unsaved changes are not in this render"}</p>
                  </div>
                ) : <Empty title={c.render_status === "failed" ? "The last render failed" : "Not rendered yet"}>{c.render_status === "failed" ? c.render_error : "Press Render to produce the video."}</Empty>
            )}
          </Card>

          <Card className="space-y-3 p-4">
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Starts at (s)" className="w-28"><NumberField step={0.05} min={0} value={draft.start} onCommit={(value) => setRange(value, draft.end, true)} /></Field>
              <Field label="Ends at (s)" className="w-28"><NumberField step={0.05} min={0} value={draft.end} onCommit={(value) => setRange(draft.start, value, true)} /></Field>
              <p className="pb-2 text-[13px] text-muted">Length <span className="tabular text-ink">{duration(length)}</span></p>
              <span className="flex-1" />
              <div className="w-56 pb-1"><Switch checked={snap} onChange={setSnap} label="Snap to gaps between words" /></div>
              <Button size="sm" variant="ghost" onClick={() => setWindowRange({ from: Math.max(0, draft.start - 45), to: Math.min(video.data.duration, draft.end + 45) })}>Show more around it</Button>
            </div>
            <Timeline from={windowRange.from} to={windowRange.to} start={draft.start} end={draft.end} time={time} envelope={timeline.data?.envelope ?? []}
              loud={timeline.data?.loud_events ?? []} cuts={timeline.data?.cuts ?? []} words={sourceWords} snap={snap}
              onSeek={seek} onDragStart={remember} onRange={(start, end) => setRange(start, end, false)} />
            {timeline.data && !timeline.data.analysed && <p className="text-xs text-faint">No waveform: this video's working files were cleared. Analyse it again to bring the waveform back.</p>}
            {(draft.settings.cuts ?? []).length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
                <span className="text-muted">Left out:</span>
                {(draft.settings.cuts ?? []).map(([from, to], index) => (
                  <span key={`${from}-${to}`} className="inline-flex items-center gap-1 rounded-full border border-line py-0.5 pl-2.5 pr-1 tabular text-ink">
                    {clock(from, true)} – {clock(to, true)}
                    <button type="button" className="rounded-full p-0.5 text-faint hover:bg-hover hover:text-ink" title="Put this part back in the clip" aria-label="Put this part back"
                      onClick={() => setSettings({ cuts: (draft.settings.cuts ?? []).filter((_, other) => other !== index) })}><XCircle className="h-3.5 w-3.5" /></button>
                  </span>
                ))}
                <span className="text-xs text-faint">The preview plays these parts; the render leaves them out.</span>
              </div>
            )}
          </Card>
        </div>

        <Card className="self-start p-4">
          <Segmented value={tab} onChange={setTab} className="mb-4 w-full [&>button]:flex-1"
            options={[{ value: "captions", label: "Captions" }, { value: "style", label: "Style" }, { value: "layout", label: "Layout" }, { value: "thumbnail", label: "Thumbnail" }, { value: "details", label: "Details" }]} />
          {tab === "captions" && <CaptionsPanel draft={draft} words={words} edited={draft.captions !== null} time={time} onSeek={seek} onEdit={editWord} onReset={() => change({ ...draft, captions: null })} />}
          {tab === "style" && <StylePanel draft={draft} options={options.data} preset={preset} setCaptions={setCaptions} setSettings={setSettings} setField={setField} />}
          {tab === "layout" && <LayoutPanel draft={draft} options={options.data} reason={plan?.reason} setSettings={setSettings} />}
          {tab === "thumbnail" && (
            <div className="space-y-5">
              <ThumbnailPanel clip={c} frameTime={() => time} />
              <TikTokCaption clip={c} />
            </div>
          )}
          {tab === "details" && <DetailsPanel draft={draft} clip={c} setField={setField} />}
        </Card>
      </div>
    </div>
  );
}
