// Clip a moment yourself: you say where it starts (and, if you like, where it ends). Forge, Claude and ChatGPT
// then do the same work as for the moments they find themselves, and the clip renders straight away.
import { Crosshair, Play, Scissors, X } from "lucide-react";
import { useState } from "react";
import { api, type Job, type Video } from "../lib/api";
import { clock, parseTime } from "../lib/format";
import { JobStages } from "./JobStages";
import { Button, Card, CardHeader, ErrorNote, Field, Input, useToast } from "./ui";

/** parts: the pieces of the source you marked to keep ([from, to] in seconds), joined in time order into one
 *  clip; partFrom: a part started with Start here and waiting for End here. Typing a start or end by hand
 *  clears the parts. */
export type MomentTimes = { start: string; end: string; parts: [number, number][]; partFrom: number | null };
export type MomentMark = "start" | "end";

export const emptyMoment: MomentTimes = { start: "", end: "", parts: [], partFrom: null };

function withParts(current: MomentTimes, parts: [number, number][]): MomentTimes {
  const merged: [number, number][] = [];
  for (const [a, b] of [...parts].sort((x, y) => x[0] - y[0])) {
    const last = merged[merged.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else merged.push([a, b]);
  }
  if (merged.length === 0) return { ...current, parts: [], start: "", end: "" };
  return { ...current, parts: merged, start: clock(merged[0][0], true), end: clock(merged[merged.length - 1][1], true) };
}

/** What a button in the player does. Start here opens a part, End here closes it and adds it to the clip. End here
 *  with no part open moves the end of the last part (or, before any part, just sets the end). */
export function markMoment(current: MomentTimes, which: MomentMark, seconds: number): MomentTimes {
  if (which === "start") {
    const next = { ...current, partFrom: seconds };
    return current.parts.length ? next : { ...next, start: clock(seconds, true), end: "" };
  }
  const from = current.partFrom;
  if (from !== null) {
    if (seconds - from < 0.5) return current;                     // End here before (or right at) the start
    return withParts({ ...current, partFrom: null }, [...current.parts, [from, seconds]]);
  }
  if (current.parts.length === 0) return { ...current, end: clock(seconds, true) };
  const parts = current.parts.map((p) => [...p] as [number, number]);
  const last = parts[parts.length - 1];
  if (seconds - last[0] < 0.5) return current;
  last[1] = seconds;
  return withParts(current, parts);
}

export function removePart(current: MomentTimes, index: number): MomentTimes {
  return withParts(current, current.parts.filter((_, i) => i !== index));
}

/** The gaps between the parts: what the server leaves out of the clip. */
export function gapsBetween(parts: [number, number][]): [number, number][] {
  const gaps: [number, number][] = [];
  for (let i = 1; i < parts.length; i++) {
    if (parts[i][0] - parts[i - 1][1] >= 0.1) gaps.push([parts[i - 1][1], parts[i][0]]);
  }
  return gaps;
}

/** The marked parts as removable chips. */
export function PartList({ times, onTimes }: { times: MomentTimes; onTimes: (next: MomentTimes) => void }) {
  if (times.parts.length === 0 && times.partFrom === null) return null;
  const total = times.parts.reduce((sum, [a, b]) => sum + (b - a), 0);
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      {times.parts.length > 0 && <span className="text-faint">In the clip, joined in order:</span>}
      {times.parts.map(([a, b], i) => (
        <span key={`${a}-${b}`} className="inline-flex items-center gap-1 rounded-md border border-line bg-panel px-2 py-0.5 text-muted">
          <span className="text-faint">{i + 1}.</span> {clock(a, true)} – {clock(b, true)}
          <button type="button" aria-label={`Remove part ${i + 1}, ${clock(a, true)} to ${clock(b, true)}`} title="Remove this part"
            className="text-faint hover:text-ink" onClick={() => onTimes(removePart(times, i))}><X className="h-3 w-3" /></button>
        </span>
      ))}
      {total > 0 && <span className="text-faint">({clock(total, true)} in all)</span>}
      {times.partFrom !== null && <span className="text-warn">Part from {clock(times.partFrom, true)}: press End here where it should stop</span>}
    </div>
  );
}

export function MomentClip({ video, times, onTimes, analyzeBody, jobs, onWatch, claudeOn, onQueued }: {
  video: Video;
  times: MomentTimes;
  onTimes: (next: MomentTimes) => void;
  analyzeBody: () => Record<string, unknown>;
  jobs: Job[];
  onWatch: (seconds: number) => void;
  claudeOn: boolean;
  onQueued: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const start = parseTime(times.start);
  const end = times.end.trim() ? parseTime(times.end) : null;
  const blocked = video.permission_status === "denied";

  const startHint = !times.start.trim() ? "Like 1:02:15, or a YouTube link copied at the moment"
    : start === null ? "Not a time ClipForge can read yet" : start >= video.duration ? `The video is only ${clock(video.duration)} long` : `Starts exactly at ${clock(start, true)}`;
  const endHint = !times.end.trim() ? (claudeOn ? "Leave empty and Claude ends it right after the payoff" : "Leave empty and it ends at the end of a sentence")
    : end === null ? "Not a time ClipForge can read yet" : start !== null && end <= start ? "Must come after the start" : `Ends at ${clock(end, true)}`;

  const make = async () => {
    setBusy(true);
    setError(null);
    try {
      const parts = times.parts;
      await api.post<Job>(`/videos/${video.id}/moments`, parts.length
        ? { start: parts[0][0], end: parts[parts.length - 1][1], skips: gapsBetween(parts), options: analyzeBody() }
        : { start: times.start.trim(), end: times.end.trim() || null, options: analyzeBody() });
      toast({ tone: "neutral", title: `Making your clip from ${clock(start ?? 0)}`,
        body: parts.length > 1 ? `Joining the ${parts.length} parts you marked. It is rendered as soon as it is ready.` : "It is rendered as soon as it is ready." });
      onTimes(emptyMoment);
      onQueued();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader title="3 · Clip a moment yourself" icon={<Scissors className="h-4 w-4 text-faint" />}
        hint="Found a good moment while watching? Say where it starts. The same work as for the AI's picks follows: end point, title, hook, score, captions, render." />
      <div className="space-y-3 px-5 pb-5">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Starts at" hint={startHint}>
            <Input value={times.start} onChange={(event) => onTimes({ ...times, start: event.target.value, parts: [], partFrom: null })} placeholder="1:02:15" aria-label="Moment starts at" />
          </Field>
          <Field label="Ends at (optional)" hint={endHint}>
            <Input value={times.end} onChange={(event) => onTimes({ ...times, end: event.target.value, parts: [], partFrom: null })} placeholder="1:02:58" aria-label="Moment ends at" />
          </Field>
        </div>
        <PartList times={times} onTimes={onTimes} />
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => onWatch(start !== null && start < video.duration ? start : 0)} title="Play the source and mark each part you want with Start here and End here. The parts are joined in order into one clip.">
            <Play className="h-4 w-4" />Watch and mark
          </Button>
          <Button variant="primary" onClick={make} busy={busy} disabled={!times.start.trim() || blocked || (times.partFrom !== null && times.parts.length > 0)}
            title={times.partFrom !== null && times.parts.length > 0 ? "A part is still open: press End here in the player first" : undefined} className="flex-1 justify-center">
            <Crosshair className="h-4 w-4" />Make this clip
          </Button>
        </div>
        {blocked && <ErrorNote error={new Error("This source is marked as not permitted for clipping. Change that above if it is wrong.")} />}
        <ErrorNote error={error} />
        {jobs.map((job) => <JobStages key={job.id} job={job} compact />)}
      </div>
    </Card>
  );
}
