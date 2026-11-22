// Everything about showing one clip: its score, why it got that score, and what you can do with it.
import {
  AlertTriangle, ArrowDown, ArrowUp, Check, CheckCircle2, ChevronDown, Clock, Copy, Download, FileText, Film,
  Heart, MessageSquareWarning, Pencil, Play, RefreshCw, Trash2, X, XCircle,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Clip, Job, Options } from "../lib/api";
import { api } from "../lib/api";
import { FEEDBACK_LABELS, PERMISSION_LABELS, bytes, clock, confidenceLabel, cx, duration, scoreTone } from "../lib/format";
import { usePatchClip, useRefreshAll } from "../lib/hooks";
import { Badge, Button, Modal, Progress, useToast, type Tone } from "./ui";

// ------------------------------------------------------------------------------------------ score ring
export function ScoreRing({ score, size = 64, manual = false }: { score: number; size?: number; manual?: boolean }) {
  const radius = size / 2 - 5;
  const length = 2 * Math.PI * radius;
  const tone = scoreTone(score);
  const colour = manual ? "var(--color-faint)" : tone === "good" ? "var(--color-good)" : tone === "warn" ? "var(--color-warn)" : "var(--color-muted)";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} title={manual ? "Made by hand: not scored" : "Estimated engagement potential (heuristic, not a prediction)"}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--color-raised)" strokeWidth={5} />
        {!manual && (
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={colour} strokeWidth={5} strokeLinecap="round"
            strokeDasharray={length} strokeDashoffset={length * (1 - Math.min(100, Math.max(0, score)) / 100)}
            className="transition-[stroke-dashoffset] duration-700 ease-out" />
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="tabular font-semibold" style={{ fontSize: size * 0.3, color: manual ? "var(--color-muted)" : "var(--color-ink)" }}>
          {manual ? "–" : Math.round(score)}
        </span>
        {size >= 56 && <span className="mt-0.5 text-[9px] uppercase tracking-wider text-faint">{manual ? "manual" : "of 100"}</span>}
      </div>
    </div>
  );
}

export function SubscoreBars({ clip, options, limit }: { clip: Clip; options?: Options; limit?: number }) {
  const dims = (options?.dimensions ?? Object.keys(clip.subscores).map((key) => ({ key, label: key }))).filter((d) => d.key in clip.subscores);
  const shown = limit ? dims.slice(0, limit) : dims;
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
      {shown.map((dim) => {
        const value = clip.subscores[dim.key] ?? 0;
        const tone = scoreTone(value);
        return (
          <div key={dim.key} className="flex items-center gap-2 text-[12.5px]">
            <dt className="w-[46%] truncate text-muted">{dim.label}</dt>
            <dd className="flex flex-1 items-center gap-2">
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-raised">
                <span className={cx("block h-full rounded-full", tone === "good" ? "bg-good" : tone === "warn" ? "bg-warn" : "bg-line-strong")} style={{ width: `${value}%` }} />
              </span>
              <span className="tabular w-7 text-right text-ink">{Math.round(value)}</span>
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function EvidenceList({ clip, onSeek }: { clip: Clip; onSeek?: (time: number) => void }) {
  if (!clip.evidence.length) return <p className="text-[13px] text-muted">{clip.explanation}</p>;
  return (
    <ul className="space-y-2">
      {clip.evidence.map((item, index) => (
        <li key={index} className="flex gap-2.5 text-[13px]">
          <span className={cx("mt-0.5 w-9 shrink-0 text-right tabular text-xs font-medium", item.impact >= 0 ? "text-good" : "text-bad")}>
            {item.impact >= 0 ? "+" : ""}{Math.round(item.impact)}
          </span>
          <div className="min-w-0">
            <p className="text-ink">{item.text}</p>
            {item.quote && <p className="mt-0.5 text-muted">“{item.quote}”</p>}
            {item.time !== null && (
              <button type="button" onClick={() => onSeek?.(item.time as number)} className="mt-0.5 inline-flex items-center gap-1 text-xs text-accent-soft hover:underline">
                <Clock className="h-3 w-3" /> source {clock(item.time, true)}
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

// -------------------------------------------------------------------------------------------- statuses
export function ReviewBadge({ status }: { status: Clip["review_status"] }) {
  if (status === "approved") return <Badge tone="good"><Check className="h-3 w-3" />Approved</Badge>;
  if (status === "rejected") return <Badge tone="bad"><X className="h-3 w-3" />Rejected</Badge>;
  return <Badge>Awaiting review</Badge>;
}

export function QcBadge({ clip }: { clip: Clip }) {
  if (!clip.qc) return null;
  const failed = clip.qc.filter((c) => c.status === "fail");
  const warned = clip.qc.filter((c) => c.status === "warn");
  if (failed.length) return <Badge tone="bad" title={failed.map((c) => c.detail).join("\n")}><XCircle className="h-3 w-3" />{failed.length} check{failed.length > 1 ? "s" : ""} failed</Badge>;
  if (warned.length) return <Badge tone="warn" title={warned.map((c) => c.detail).join("\n")}><AlertTriangle className="h-3 w-3" />{warned.length} warning{warned.length > 1 ? "s" : ""}</Badge>;
  return <Badge tone="good" title="All export checks passed"><CheckCircle2 className="h-3 w-3" />Checks passed</Badge>;
}

export function RightsBadge({ status }: { status: string }) {
  const tone: Tone = status === "owned" || status === "licensed" || status === "permission" ? "good" : status === "denied" ? "bad" : "warn";
  return <Badge tone={tone} title="Whether you are allowed to clip and post this source. Credit does not replace permission.">{PERMISSION_LABELS[status] ?? status}</Badge>;
}

// --------------------------------------------------------------------------------------------- actions
export function useClipActions() {
  const patch = usePatchClip();
  const refresh = useRefreshAll();
  const toast = useToast();
  const fail = (title: string) => (error: unknown) => toast({ tone: "bad", title, body: (error as Error).message });
  return {
    review: (clip: Clip, status: Clip["review_status"]) =>
      patch.mutateAsync({ id: clip.id, body: { review_status: clip.review_status === status ? "pending" : status } }).catch(fail("Could not update the clip")),
    favorite: (clip: Clip) => patch.mutateAsync({ id: clip.id, body: { favorite: !clip.favorite } }).catch(fail("Could not update the clip")),
    render: (clip: Clip) => api.post<Job>(`/clips/${clip.id}/render`).then(() => { refresh(); toast({ tone: "neutral", title: "Rendering queued", body: clip.title }); }).catch(fail("Could not start the render")),
    remove: (clip: Clip) => api.del(`/clips/${clip.id}`).then(refresh).catch(fail("Could not delete the clip")),
    feedback: (clip: Clip, label: string) =>
      api.post(`/clips/${clip.id}/feedback`, { label }).then(() => { refresh(); toast({ tone: "good", title: "Feedback saved", body: `${FEEDBACK_LABELS[label]}. Future rankings will take it into account.` }); }).catch(fail("Could not save feedback")),
    duplicate: (clip: Clip) => api.post<Clip>(`/clips/${clip.id}/duplicate`).then((copy) => { refresh(); toast({ tone: "good", title: "Variant created", body: "Open it in the editor to change the hook, style or cut." }); return copy; }).catch(fail("Could not create a variant")),
    move: (ids: number[]) => api.post("/clips/reorder", { ids }).then(refresh).catch(fail("Could not reorder")),
  };
}

export function FeedbackMenu({ clip, onPick }: { clip: Clip; onPick: (label: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)} aria-expanded={open} title="Tell ClipForge what you think of this suggestion">
        <MessageSquareWarning className="h-4 w-4" />Feedback{clip.feedback.length > 0 && <span className="text-faint">({clip.feedback.length})</span>}<ChevronDown className="h-3.5 w-3.5" />
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-56 rounded-xl border border-line-strong bg-raised p-1 shadow-xl animate-rise">
            {Object.entries(FEEDBACK_LABELS).map(([key, label]) => (
              <button key={key} type="button" onClick={() => { setOpen(false); onPick(key); }}
                className="flex w-full items-center justify-between rounded-lg px-3 py-1.5 text-left text-[13px] text-ink hover:bg-hover">
                {label}
                {clip.feedback.includes(key) && <Check className="h-3.5 w-3.5 text-good" />}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------- thumbnail
export function ClipThumb({ clip, job, onOpen, className }: { clip: Clip; job?: Job; onOpen?: () => void; className?: string }) {
  const working = clip.render_status === "queued" || clip.render_status === "rendering";
  const ratio = clip.out_width && clip.out_height ? clip.out_width / clip.out_height : 9 / 16;
  return (
    <button type="button" onClick={onOpen} className={cx("group relative block overflow-hidden rounded-xl border border-line bg-black", className)} style={{ aspectRatio: String(ratio) }} aria-label={`Preview ${clip.title}`}>
      {clip.thumb_url ? (
        <img src={clip.thumb_url} alt="" className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" loading="lazy" />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-faint">
          <Film className="h-6 w-6" />
          <span className="text-[11px]">{working ? "Rendering…" : clip.render_status === "failed" ? "Render failed" : "Not rendered"}</span>
        </div>
      )}
      {clip.render_status === "ready" && (
        <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/35">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-white/90 opacity-0 shadow-lg transition-opacity group-hover:opacity-100"><Play className="h-5 w-5 translate-x-0.5 fill-black text-black" /></span>
        </span>
      )}
      {working && (
        <span className="absolute inset-x-2 bottom-2"><Progress value={job?.progress ?? 0} busy /></span>
      )}
      <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 py-0.5 text-[11px] tabular text-white">{duration(clip.out_duration || clip.end - clip.start)}</span>
    </button>
  );
}

// ------------------------------------------------------------------------------------------ result card
export function ClipCard({
  clip, position, options, job, onOpen, onPeek, onMove,
}: {
  clip: Clip; position?: number; options?: Options; job?: Job; onOpen: () => void; onPeek: (time: number) => void;
  onMove?: (direction: -1 | 1) => void;
}) {
  const actions = useClipActions();
  const [expanded, setExpanded] = useState(false);
  const manual = clip.analysis_source === "manual";
  const category = options?.categories[clip.category] ?? clip.category;

  return (
    <article className={cx("rounded-2xl border bg-panel p-4 transition-colors animate-rise", clip.review_status === "approved" ? "border-good/30" : clip.review_status === "rejected" ? "border-line opacity-70" : "border-line hover:border-line-strong")}>
      <div className="flex gap-4">
        <ClipThumb clip={clip} job={job} onOpen={onOpen} className="w-[104px] shrink-0 self-start" />
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                {position !== undefined && <span className="tabular text-xs font-semibold text-faint">#{position}</span>}
                {category && <Badge tone="accent">{category}</Badge>}
                {clip.analysis_source === "claude" && <Badge tone="info" title="Claude chose this moment; the reason is below">Picked by Claude</Badge>}
                {clip.origin === "variant" && <Badge tone="info">{clip.variant_label || "Variant"}</Badge>}
                {clip.manual_rank !== null && <Badge title="You placed this clip yourself">Your order</Badge>}
                <ReviewBadge status={clip.review_status} />
                <QcBadge clip={clip} />
              </div>
              <h3 className="mt-1.5 line-clamp-2 text-[15px] font-semibold leading-snug text-ink">{clip.title}</h3>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-muted">
                <button type="button" onClick={() => onPeek(clip.start)} className="inline-flex items-center gap-1 text-accent-soft hover:underline" title="Play the original video from here">
                  <Clock className="h-3.5 w-3.5" />{clock(clip.start)} – {clock(clip.end)}
                </button>
                <span>{duration(clip.end - clip.start)}</span>
                {!manual && <span title="How much the available data supports this estimate">{confidenceLabel(clip.confidence)} ({Math.round(clip.confidence * 100)}%)</span>}
              </div>
            </div>
            <ScoreRing score={clip.score} manual={manual} />
          </div>

          <p className={cx("mt-2 text-[13px] text-muted", !expanded && "line-clamp-2")}>{clip.explanation}</p>
          {clip.render_status === "failed" && <p className="mt-1.5 break-words text-[13px] text-bad">Render failed: {clip.render_error}</p>}

          {expanded && (
            <div className="mt-3 space-y-4 rounded-xl border border-line bg-bg/60 p-3.5 animate-fade">
              {!manual && <SubscoreBars clip={clip} options={options} />}
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Evidence</p>
                <EvidenceList clip={clip} onSeek={onPeek} />
              </div>
              {clip.transcript_text && (
                <div>
                  <p className="mb-1 text-xs font-medium uppercase tracking-wider text-faint">What is said</p>
                  <p className="text-[13px] leading-relaxed text-muted">{clip.transcript_text}</p>
                </div>
              )}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <Button size="sm" variant={clip.review_status === "approved" ? "good" : "secondary"} onClick={() => actions.review(clip, "approved")}><Check className="h-4 w-4" />{clip.review_status === "approved" ? "Approved" : "Approve"}</Button>
            <Button size="sm" variant={clip.review_status === "rejected" ? "danger" : "secondary"} onClick={() => actions.review(clip, "rejected")}><X className="h-4 w-4" />{clip.review_status === "rejected" ? "Rejected" : "Reject"}</Button>
            <Link to={`/editor/${clip.id}`}><Button size="sm"><Pencil className="h-4 w-4" />Edit</Button></Link>
            {clip.render_status === "ready" ? (
              <a href={`/api/clips/${clip.id}/file?download=1`}><Button size="sm"><Download className="h-4 w-4" />Download</Button></a>
            ) : (
              <Button size="sm" onClick={() => actions.render(clip)} disabled={clip.render_status === "queued" || clip.render_status === "rendering"}>
                <RefreshCw className="h-4 w-4" />{clip.render_status === "failed" ? "Render again" : clip.render_status === "none" ? "Render" : "Rendering…"}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
              Why this score<ChevronDown className={cx("h-3.5 w-3.5 transition-transform", expanded && "rotate-180")} />
            </Button>
            <span className="flex-1" />
            {onMove && (
              <>
                <Button size="icon" variant="ghost" onClick={() => onMove(-1)} title="Move up in your ranking" aria-label="Move up"><ArrowUp className="h-4 w-4" /></Button>
                <Button size="icon" variant="ghost" onClick={() => onMove(1)} title="Move down in your ranking" aria-label="Move down"><ArrowDown className="h-4 w-4" /></Button>
              </>
            )}
            <Button size="icon" variant="ghost" onClick={() => actions.favorite(clip)} title={clip.favorite ? "Remove from favourites" : "Add to favourites"} aria-label="Favourite" aria-pressed={clip.favorite}>
              <Heart className={cx("h-4 w-4", clip.favorite && "fill-accent text-accent")} />
            </Button>
            <FeedbackMenu clip={clip} onPick={(label) => actions.feedback(clip, label)} />
          </div>
        </div>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------------------------------- clip viewer
export function ClipViewer({ clip, options, onClose, onPeek }: { clip: Clip | null; options?: Options; onClose: () => void; onPeek: (time: number) => void }) {
  const actions = useClipActions();
  const toast = useToast();
  if (!clip) return null;
  const manual = clip.analysis_source === "manual";
  const copyKit = async () => {
    try {
      const response = await fetch(`/api/clips/${clip.id}/post-kit`);
      await navigator.clipboard.writeText(await response.text());
      toast({ tone: "good", title: "Post text copied", body: "Title, description, hashtags and source details are on your clipboard." });
    } catch {
      toast({ tone: "bad", title: "Could not copy", body: "Use the post text file instead." });
    }
  };
  return (
    <Modal open onClose={onClose} title={clip.title} wide>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,320px)_1fr]">
        <div>
          {clip.render_status === "ready" ? (
            <video key={clip.file_url} src={clip.file_url} poster={clip.thumb_url} controls autoPlay playsInline className="mx-auto max-h-[70vh] w-full rounded-xl border border-line" />
          ) : (
            <div className="flex aspect-[9/16] w-full flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-line text-center text-muted">
              <Film className="h-8 w-8 text-faint" />
              <p className="px-6 text-[13px]">{clip.render_status === "failed" ? `The last render failed: ${clip.render_error}` : "This clip has not been rendered yet."}</p>
              <Button variant="primary" onClick={() => actions.render(clip)} disabled={clip.render_status === "queued" || clip.render_status === "rendering"}>
                {clip.render_status === "queued" || clip.render_status === "rendering" ? "Rendering…" : "Render it"}
              </Button>
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            {clip.render_status === "ready" && (
              <>
                <a href={`/api/clips/${clip.id}/file?download=1`}><Button size="sm" variant="primary"><Download className="h-4 w-4" />MP4</Button></a>
                <a href={`/api/clips/${clip.id}/subtitles.srt`}><Button size="sm"><FileText className="h-4 w-4" />SRT</Button></a>
                <a href={`/api/clips/${clip.id}/subtitles.vtt`}><Button size="sm"><FileText className="h-4 w-4" />VTT</Button></a>
              </>
            )}
            <Button size="sm" onClick={copyKit}><Copy className="h-4 w-4" />Copy post text</Button>
          </div>
          {clip.render_status === "ready" && (
            <p className="mt-2 text-xs text-faint">{clip.out_width}×{clip.out_height} · {duration(clip.out_duration)} · {bytes(clip.file_size)}</p>
          )}
        </div>

        <div className="min-w-0 space-y-5">
          <div className="flex items-start gap-4">
            <ScoreRing score={clip.score} size={84} manual={manual} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                {clip.analysis_source === "claude" && <Badge tone="info">Picked by Claude</Badge>}
                <ReviewBadge status={clip.review_status} />
                <QcBadge clip={clip} />
                {clip.permission_status && <RightsBadge status={clip.permission_status} />}
              </div>
              <p className="mt-2 text-[13px] text-muted">{clip.explanation}</p>
              {!manual && (
                <p className="mt-1.5 text-xs text-faint">
                  {confidenceLabel(clip.confidence)} ({Math.round(clip.confidence * 100)}%). A heuristic estimate of engagement potential, not a prediction of views.
                </p>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <Button size="sm" variant={clip.review_status === "approved" ? "good" : "secondary"} onClick={() => actions.review(clip, "approved")}><Check className="h-4 w-4" />Approve</Button>
            <Button size="sm" variant={clip.review_status === "rejected" ? "danger" : "secondary"} onClick={() => actions.review(clip, "rejected")}><X className="h-4 w-4" />Reject</Button>
            <Link to={`/editor/${clip.id}`} onClick={onClose}><Button size="sm"><Pencil className="h-4 w-4" />Open in editor</Button></Link>
            <Button size="sm" onClick={() => actions.duplicate(clip)} title="Copy this clip to try another hook, style or cut">Make a variant</Button>
            <Button size="sm" onClick={() => actions.render(clip)}><RefreshCw className="h-4 w-4" />Render again</Button>
            <Button size="sm" variant="ghost" onClick={() => { if (window.confirm(`Delete “${clip.title}” and its rendered file?`)) { actions.remove(clip); onClose(); } }}><Trash2 className="h-4 w-4" />Delete</Button>
          </div>

          {!manual && (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Score breakdown</p>
              <SubscoreBars clip={clip} options={options} />
            </div>
          )}
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Evidence</p>
            <EvidenceList clip={clip} onSeek={(time) => { onClose(); onPeek(time); }} />
          </div>
          {clip.qc && (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Export checks</p>
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
          <div>
            <p className="mb-1 text-xs font-medium uppercase tracking-wider text-faint">Source</p>
            <p className="text-[13px] text-muted">
              {clip.video_title}{clip.creator_name && ` · ${clip.creator_name}`} ·{" "}
              <button type="button" className="text-accent-soft hover:underline" onClick={() => { onClose(); onPeek(clip.start); }}>{clock(clip.start, true)} – {clock(clip.end, true)}</button>
            </p>
            {clip.hashtags.length > 0 && <p className="mt-1 text-[13px] text-info">{clip.hashtags.join(" ")}</p>}
          </div>
        </div>
      </div>
    </Modal>
  );
}

/** Play the original video from a timestamp, to check a suggestion against its source. */
export function SourcePeek({ videoId, time, title, onClose }: { videoId: number | null; time: number | null; title?: string; onClose: () => void }) {
  if (videoId === null || time === null) return null;
  return (
    <Modal open onClose={onClose} title={`Source${title ? `: ${title}` : ""} at ${clock(time, true)}`} wide>
      <video src={`/api/videos/${videoId}/stream#t=${Math.max(0, time - 0.5).toFixed(2)}`} controls autoPlay playsInline className="max-h-[72vh] w-full rounded-xl border border-line" />
      <p className="mt-2 text-xs text-faint">Playing the untouched source from just before the timestamp.</p>
    </Modal>
  );
}
