import { Activity, CheckCircle2, Clapperboard, Cpu, Download, Film, HardDrive, Hourglass, Link2Off, Scissors, Sparkles, XCircle } from "lucide-react";
import { logOf, WorkLogLines } from "../components/WorkLog";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ClipThumb, ClipViewer, ScoreRing, SourcePeek } from "../components/clips";
import { Badge, Button, Card, CardHeader, Empty, ErrorNote, Progress, Spinner } from "../components/ui";
import type { Clip, Job } from "../lib/api";
import { ago, bytes, cx, duration } from "../lib/format";
import { useDashboard, useHardware, useOptions, useStats } from "../lib/hooks";

function Stat({ label, value, hint, icon, tone }: { label: string; value: ReactNode; hint?: string; icon: ReactNode; tone?: "accent" }) {
  return (
    <div className="rounded-2xl border border-line bg-panel p-4">
      <div className="flex items-center justify-between text-muted">
        <span className="text-[12.5px]">{label}</span>
        <span className={cx(tone === "accent" ? "text-accent" : "text-faint")}>{icon}</span>
      </div>
      <p className="mt-2 font-display text-[28px] font-semibold leading-none tabular text-ink">{value}</p>
      {hint && <p className="mt-1.5 text-xs text-faint">{hint}</p>}
    </div>
  );
}

function Meter({ label, value, detail }: { label: string; value: number | null; detail: string }) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-[13px]">
        <span className="text-muted">{label}</span>
        <span className="tabular text-ink">{value === null ? "n/a" : `${Math.round(value)}%`}</span>
      </div>
      <Progress value={(value ?? 0) / 100} tone={(value ?? 0) > 90 ? "bad" : "accent"} />
      <p className="mt-1 truncate text-xs text-faint" title={detail}>{detail}</p>
    </div>
  );
}

function JobRow({ job }: { job: Job }) {
  const tone = job.status === "succeeded" ? "good" : job.status === "failed" ? "bad" : job.status === "cancelled" ? "neutral" : "accent";
  const took = job.started_at && job.finished_at ? (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000 : null;
  return (
    <li className="py-2">
      <div className="flex items-center gap-3">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-raised text-faint">
        {job.type === "analyze" ? <Sparkles className="h-3.5 w-3.5" /> : job.type === "import" ? <Download className="h-3.5 w-3.5" /> : job.type === "moment" ? <Scissors className="h-3.5 w-3.5" /> : <Clapperboard className="h-3.5 w-3.5" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] text-ink">{job.type === "analyze" ? "Analysis" : job.type === "import" ? "Import from a link" : job.type === "moment" ? "Your own moment" : "Render"} #{job.id}{job.status === "running" && job.message ? ` · ${job.message}` : ""}</p>
        {job.status === "running" ? <Progress value={job.progress} busy className="mt-1" /> : (
          <p className="truncate text-xs text-faint">{job.status === "failed" ? job.error : `${ago(job.finished_at ?? job.created_at)}${took !== null ? ` · took ${duration(took)}` : ""}`}</p>
        )}
      </div>
      <Badge tone={tone}>{job.status}</Badge>
      </div>
      {logOf(job).length > 0 && <WorkLogLines lines={logOf(job)} className="mt-1.5 pl-10" />}
    </li>
  );
}

export default function DashboardPage() {
  const dashboard = useDashboard();
  const stats = useStats();
  const hardware = useHardware();
  const options = useOptions();
  const [viewing, setViewing] = useState<Clip | null>(null);
  const [peek, setPeek] = useState<{ video: number; time: number } | null>(null);

  if (dashboard.isLoading) return <div className="flex justify-center py-24"><Spinner className="h-6 w-6" /></div>;
  if (dashboard.isError || !dashboard.data) return <ErrorNote error={dashboard.error ?? new Error("Could not load the dashboard")} />;
  const d = dashboard.data;
  const fresh = d.videos.total === 0;
  const gpu = stats.data?.gpu;
  const hw = hardware.data?.hardware;
  const storageTotal = Math.max(1, d.storage.total);

  return (
    <div className="space-y-6">
      {fresh && (
        <div className="relative overflow-hidden rounded-3xl border border-line bg-panel px-7 py-9">
          <div className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-accent/15 blur-3xl" />
          <h2 className="font-display text-2xl font-semibold tracking-tight">Turn a long video into clips worth posting</h2>
          <p className="mt-2 max-w-xl text-[14px] text-muted">Add a recording, press Generate, and ClipForge finds the moments that stand on their own, cuts them vertical with captions, and tells you why each one was picked. Everything runs on this PC.</p>
          <Link to="/clipper"><Button variant="primary" size="lg" className="mt-5"><Sparkles className="h-4 w-4" />Open the AI Clipper</Button></Link>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Videos processed" value={d.videos.analyzed} hint={`${d.videos.total} added · ${d.videos.hours} h of footage`} icon={<Film className="h-4 w-4" />} />
        <Stat label="Clips generated" value={d.clips.total} hint={d.clips.total ? `average score ${d.clips.average_score}` : "none yet"} icon={<Sparkles className="h-4 w-4" />} tone="accent" />
        <Stat label="Awaiting review" value={d.clips.awaiting_review} hint={`${d.clips.approved} approved · ${d.clips.rejected} rejected`} icon={<Hourglass className="h-4 w-4" />} />
        <Stat label="Rendered" value={d.clips.rendered} hint={`${d.clips.favorites} favourites`} icon={<CheckCircle2 className="h-4 w-4" />} />
        <Stat label="Scheduled" value="–" hint="Scheduling is not built yet" icon={<Hourglass className="h-4 w-4" />} />
        <Stat label="Verified views" value="–" hint="No connected accounts" icon={<Link2Off className="h-4 w-4" />} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-6">
          <Card>
            <CardHeader title="Best clips" hint="Highest estimated engagement potential, excluding rejected ones." action={<Link to="/library"><Button size="sm" variant="ghost">Open library</Button></Link>} />
            <div className="px-5 pb-5">
              {d.top_clips.length === 0 ? (
                <Empty title="No clips yet">Generate your first clips in the AI Clipper.</Empty>
              ) : (
                <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                  {d.top_clips.map((clip) => (
                    <li key={clip.id} className="min-w-0">
                      <div className="relative">
                        <ClipThumb clip={clip} onOpen={() => setViewing(clip)} className="w-full" />
                        <div className="pointer-events-none absolute left-1.5 top-1.5 rounded-full bg-black/60 p-0.5 backdrop-blur"><ScoreRing score={clip.score} size={34} manual={clip.analysis_source === "manual"} /></div>
                      </div>
                      <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-snug text-ink">{clip.title}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Processing activity" icon={<Activity className="h-4 w-4 text-faint" />}
              hint={`${d.queue.running} running · ${d.queue.waiting} waiting${d.queue.failed_this_week ? ` · ${d.queue.failed_this_week} failed this week` : ""}`} />
            <div className="px-5 pb-4">
              {d.recent_jobs.length === 0 ? <p className="pb-2 text-[13px] text-muted">No jobs have run yet.</p> : (
                <ul className="divide-y divide-line">{d.recent_jobs.map((job) => <JobRow key={job.id} job={job} />)}</ul>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="This PC right now" icon={<Cpu className="h-4 w-4 text-faint" />} />
            <div className="space-y-4 px-5 pb-5">
              <Meter label="Processor" value={stats.data?.cpu_percent ?? null} detail={hw?.cpu.name ?? ""} />
              <Meter label="Memory" value={stats.data?.memory.used_percent ?? null} detail={stats.data ? `${(stats.data.memory.total_gb - stats.data.memory.available_gb).toFixed(1)} of ${stats.data.memory.total_gb} GB in use` : ""} />
              {gpu ? (
                <Meter label="Graphics card" value={gpu.utilization_percent} detail={`${gpu.name} · ${gpu.memory_used_mb ?? "?"} of ${gpu.memory_total_mb ?? "?"} MB · ${gpu.temperature_c ?? "?"} °C`} />
              ) : <p className="text-xs text-faint">No NVIDIA graphics card detected.</p>}
              {hardware.data && (
                <div className="space-y-1.5 border-t border-line pt-3 text-[12.5px]">
                  <p className="flex items-center gap-1.5 text-muted">
                    {hw?.encoders.h264_nvenc?.ok ? <CheckCircle2 className="h-3.5 w-3.5 text-good" /> : <XCircle className="h-3.5 w-3.5 text-faint" />}
                    Video encoding: {hardware.data.recommended.video_encoder === "h264_nvenc" ? "graphics card (tested)" : "processor"}
                  </p>
                  <p className="flex items-center gap-1.5 text-muted">
                    {hw?.whisper_cuda.ok ? <CheckCircle2 className="h-3.5 w-3.5 text-good" /> : <XCircle className="h-3.5 w-3.5 text-faint" />}
                    Speech-to-text: {hardware.data.recommended.whisper_device === "cuda" ? "graphics card (tested)" : "processor"} · {hardware.data.recommended.whisper_model ?? "no model installed"}
                  </p>
                </div>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="Compute this week" hint="Measured job time on this PC, last 7 days." />
            <div className="grid grid-cols-3 gap-3 px-5 pb-5 text-center">
              <div><p className="font-display text-xl font-semibold tabular">{duration(d.compute.analyze_seconds_7d)}</p><p className="text-xs text-faint">analysing</p></div>
              <div><p className="font-display text-xl font-semibold tabular">{duration(d.compute.render_seconds_7d)}</p><p className="text-xs text-faint">rendering</p></div>
              <div><p className="font-display text-xl font-semibold tabular">{d.compute.jobs_7d}</p><p className="text-xs text-faint">jobs done</p></div>
            </div>
          </Card>

          <Card>
            <CardHeader title="Storage" icon={<HardDrive className="h-4 w-4 text-faint" />} hint={`${bytes(d.storage.total)} used by ClipForge · ${bytes(d.storage.disk_free)} free on the disk`} />
            <div className="px-5 pb-5">
              <div className="flex h-2 overflow-hidden rounded-full bg-raised">
                {(["uploads", "clips", "work", "models"] as const).map((area, index) => (
                  <span key={area} title={`${area}: ${bytes(d.storage.areas[area] ?? 0)}`} style={{ width: `${((d.storage.areas[area] ?? 0) / storageTotal) * 100}%` }}
                    className={["bg-accent", "bg-good", "bg-info", "bg-line-strong"][index]} />
                ))}
              </div>
              <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted">
                {[["uploads", "Source videos", "bg-accent"], ["clips", "Rendered clips", "bg-good"], ["work", "Working files", "bg-info"], ["models", "AI models", "bg-line-strong"]].map(([key, label, colour]) => (
                  <li key={key} className="flex items-center justify-between gap-2"><span className="flex items-center gap-1.5"><span className={cx("h-2 w-2 rounded-full", colour)} />{label}</span><span className="tabular text-ink">{bytes(d.storage.areas[key] ?? 0)}</span></li>
                ))}
              </ul>
            </div>
          </Card>

          <Card>
            <CardHeader title="Publishing" icon={<Link2Off className="h-4 w-4 text-faint" />} />
            <div className="px-5 pb-5 text-[13px] text-muted">
              <p><span className="tabular text-ink">{d.publishing.connected_accounts}</span> connected accounts · <span className="tabular text-ink">{d.publishing.scheduled}</span> scheduled · <span className="tabular text-ink">{d.publishing.published}</span> published{d.publishing.in_drafts ? <> · <span className="tabular text-ink">{d.publishing.in_drafts}</span> in TikTok drafts</> : null}{d.publishing.failed ? <> · <span className="tabular text-bad">{d.publishing.failed}</span> failed</> : null}</p>
              <p className="mt-1">{d.publishing.note} <Link to="/publishing" className="text-accent-soft hover:underline">Open the Publishing Studio.</Link></p>
            </div>
          </Card>
        </div>
      </div>

      <ClipViewer clip={viewing} options={options.data} onClose={() => setViewing(null)} onPeek={(time) => viewing && setPeek({ video: viewing.video_id, time })} />
      <SourcePeek videoId={peek?.video ?? null} time={peek?.time ?? null} onClose={() => setPeek(null)} />
    </div>
  );
}
