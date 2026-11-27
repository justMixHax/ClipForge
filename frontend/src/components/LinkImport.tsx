// Import one public video from a link: check what it is, say why you may clip it, then download it.
// ClipForge never signs in to a site and never reads browser cookies, so only public videos work.
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Download, Link2, ShieldAlert } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { api, type Job, type LinkInfo } from "../lib/api";
import { bytes, duration } from "../lib/format";
import { useActiveJobs, useRefreshAll } from "../lib/hooks";
import { JobStages } from "./JobStages";
import { Button, ErrorNote, Field, Input, Select, Switch } from "./ui";

const QUALITIES = [
  { value: 720, label: "Up to 720p (smaller, faster)" },
  { value: 1080, label: "Up to 1080p (recommended)" },
  { value: 1440, label: "Up to 1440p" },
  { value: 2160, label: "Up to 4K (large, slow)" },
];
const BUSY = (job: Job | undefined | null) => job?.status === "queued" || job?.status === "running";

export function LinkImport({ analyzeBody, onImported, kind }: {
  analyzeBody: () => Record<string, unknown>;   // the current "How to clip it" settings
  onImported: (videoId: number) => void;
  kind?: ReactNode;                              // the choice of what to look for, shown with the video found
}) {
  const refresh = useRefreshAll();
  const active = useActiveJobs();
  const [url, setUrl] = useState("");
  const [quality, setQuality] = useState(1080);
  const [found, setFound] = useState<LinkInfo | null>(null);
  const [checking, setChecking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [rights, setRights] = useState("");
  const [note, setNote] = useState("");
  const [thenClip, setThenClip] = useState(true);
  const [jobId, setJobId] = useState<number | null>(null);
  const handled = useRef<number | null>(null);

  // An import that is already running (for example after reloading the page) is picked up again.
  const running = active.data?.find((job) => job.type === "import");
  useEffect(() => {
    if (running && jobId === null) setJobId(running.id);
  }, [running, jobId]);

  const tracked = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<Job>(`/jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (state) => (BUSY(state.state.data) || !state.state.data ? 1000 : false),
  });
  const job = tracked.data ?? null;

  useEffect(() => {
    if (!job || job.status !== "succeeded" || handled.current === job.id) return;
    handled.current = job.id;
    const videoId = Number(job.result?.video_id);
    refresh();
    setJobId(null);
    setFound(null);
    setUrl("");
    if (videoId) onImported(videoId);
  }, [job, onImported, refresh]);

  const check = async (height = quality) => {
    setError(null);
    setChecking(true);
    try {
      const info = await api.post<LinkInfo>("/videos/inspect-url", { url, max_height: height });
      setFound(info);
      if (!note && info.license) setNote(info.license);
    } catch (e) {
      setFound(null);
      setError(e);
    } finally {
      setChecking(false);
    }
  };

  const start = async () => {
    if (!found || !rights) return;
    setError(null);
    setStarting(true);
    try {
      const created = await api.post<Job>("/videos/import-url", {
        url: found.url, permission_status: rights, permission_note: note, creator_name: found.creator,
        max_height: quality, analyze: thenClip ? analyzeBody() : null,
      });
      setJobId(created.id);
      refresh();
    } catch (e) {
      setError(e);
    } finally {
      setStarting(false);
    }
  };

  const reset = () => { setFound(null); setError(null); setRights(""); setNote(""); };

  if (job && job.status !== "succeeded") {
    return (
      <div className="space-y-3 rounded-xl border border-line bg-bg/60 p-3">
        <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-faint"><Download className="h-3.5 w-3.5" />Importing from a link</p>
        <JobStages job={job} />
        {!BUSY(job) && <Button size="sm" variant="ghost" onClick={() => { setJobId(null); reset(); }}>Start over with another link</Button>}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Field label="Or paste a link" hint="YouTube, Twitch and Kick recordings, and most video sites. Public videos only: ClipForge never signs in to a site.">
        <div className="flex gap-2">
          <Input value={url} onChange={(event) => { setUrl(event.target.value); if (found) reset(); }} placeholder="https://www.youtube.com/watch?v=…"
            spellCheck={false} inputMode="url" aria-label="Video link" onKeyDown={(event) => event.key === "Enter" && url.trim() && check()} />
          <Button onClick={() => check()} busy={checking} disabled={!url.trim()}><Link2 className="h-4 w-4" />Check link</Button>
        </div>
      </Field>
      <ErrorNote error={error} />

      {found && (
        <div className="space-y-3 rounded-xl border border-line bg-bg/60 p-3">
          <div>
            <p className="line-clamp-2 text-sm font-medium text-ink">{found.title}</p>
            <p className="mt-0.5 text-xs text-muted">
              {[found.creator, found.site !== "Generic" ? found.site : "", found.duration ? duration(found.duration) : "",
                found.approx_bytes ? `about ${bytes(found.approx_bytes)}${found.height ? ` at ${found.height}p` : ""}` : "size not stated by the site"]
                .filter(Boolean).join(" · ")}
            </p>
            {found.license && <p className="mt-1 text-xs text-muted">The site lists it as: {found.license}</p>}
          </div>

          {found.existing_video_id !== null && (
            <p className="flex items-center gap-2 text-xs text-info">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />This video is already in your library.
              <button type="button" className="text-accent-soft hover:underline" onClick={() => onImported(found.existing_video_id as number)}>Open it</button>
            </p>
          )}
          {found.width && found.height && Math.min(found.width, found.height) < 700 && (
            <p className="text-xs text-warn" role="note">
              Low quality: this download is only {found.width}×{found.height}. Clips are enlarged to 1080 pixels wide, so they will look soft.
              {found.height < quality ? " The site offers nothing sharper for this video." : " Choose a higher quality below if the site has one."}
            </p>
          )}
          {found.over_limit && (
            <ErrorNote error={new Error(`About ${bytes(found.approx_bytes ?? 0)} at this quality, over the ${found.limit_gb} GB limit for imports. Choose a lower quality.`)} />
          )}

          {kind}
          <Field label="Why may you clip this video?" hint="A video being public does not make it free to repost. Your answer is saved with the video and added to every clip's post text.">
            <Select value={rights} onChange={(event) => setRights(event.target.value)} aria-label="Why may you clip this video?">
              <option value="" disabled>Choose…</option>
              <option value="owned">It is my own content</option>
              <option value="licensed">I have a licence, or it is a campaign</option>
              <option value="permission">The creator said I may</option>
            </Select>
          </Field>
          <Field label="Where the permission comes from (optional)">
            <Input value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} placeholder="e.g. my channel, campaign page, message from the creator" />
          </Field>
          <Field label="Quality to download">
            <Select value={quality} onChange={(event) => { const height = Number(event.target.value); setQuality(height); check(height); }} aria-label="Quality to download">
              {QUALITIES.map((q) => <option key={q.value} value={q.value}>{q.label}</option>)}
            </Select>
          </Field>
          <Switch checked={thenClip} onChange={setThenClip} label="Find clips as soon as it has downloaded" hint="Uses the settings under “How to clip it”. Off: it just lands in your library." />

          {!rights && <p className="flex items-start gap-1.5 text-xs text-warn"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />Choose why you may clip it to enable the import.</p>}
          <div className="flex gap-2">
            <Button variant="primary" onClick={start} busy={starting} disabled={!rights || found.over_limit || checking}><Download className="h-4 w-4" />Import video</Button>
            <Button variant="ghost" onClick={() => { reset(); setUrl(""); }}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  );
}
