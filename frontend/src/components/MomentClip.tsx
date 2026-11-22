// Clip a moment yourself: you say where it starts (and, if you like, where it ends). Forge, Claude and ChatGPT
// then do the same work as for the moments they find themselves, and the clip renders straight away.
import { Crosshair, Play, Scissors } from "lucide-react";
import { useState } from "react";
import { api, type Job, type Video } from "../lib/api";
import { clock, parseTime } from "../lib/format";
import { JobStages } from "./JobStages";
import { Button, Card, CardHeader, ErrorNote, Field, Input, useToast } from "./ui";

export type MomentTimes = { start: string; end: string };

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
      await api.post<Job>(`/videos/${video.id}/moments`, { start: times.start.trim(), end: times.end.trim() || null, options: analyzeBody() });
      toast({ tone: "neutral", title: `Making your clip from ${clock(start ?? 0)}`, body: "It is rendered as soon as it is ready." });
      onTimes({ start: "", end: "" });
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
            <Input value={times.start} onChange={(event) => onTimes({ ...times, start: event.target.value })} placeholder="1:02:15" aria-label="Moment starts at" />
          </Field>
          <Field label="Ends at (optional)" hint={endHint}>
            <Input value={times.end} onChange={(event) => onTimes({ ...times, end: event.target.value })} placeholder="1:02:58" aria-label="Moment ends at" />
          </Field>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => onWatch(start !== null && start < video.duration ? start : 0)} title="Play the source and mark the moment with Start here and End here">
            <Play className="h-4 w-4" />Watch and mark
          </Button>
          <Button variant="primary" onClick={make} busy={busy} disabled={!times.start.trim() || blocked} className="flex-1 justify-center">
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
