// Every clip, across all videos: search, filter, sort, select many, act on them together.
import { Check, Download, Folders, FolderOutput, Heart, Pencil, RefreshCw, Search, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ClipThumb, ClipViewer, QcBadge, ReviewBadge, RightsBadge, ScoreRing, SourcePeek } from "../components/clips";
import { ProjectsModal } from "../components/projects";
import { Badge, Button, Empty, ErrorNote, Input, Select, Spinner, useToast } from "../components/ui";
import { api, type Clip } from "../lib/api";
import { clock, cx, duration } from "../lib/format";
import { useActiveJobs, useClips, useOptions, useProjects, useRefreshAll, useVideos } from "../lib/hooks";

export default function LibraryPage() {
  const [q, setQ] = useState("");
  const [review, setReview] = useState("");
  const [render, setRender] = useState("");
  const [videoId, setVideoId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [managing, setManaging] = useState(false);
  const [favorite, setFavorite] = useState(false);
  const [sort, setSort] = useState("score");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [viewing, setViewing] = useState<number | null>(null);
  const [peek, setPeek] = useState<{ video: number; time: number } | null>(null);
  const [busy, setBusy] = useState(false);

  const filters = useMemo(() => ({
    q: q.trim() || undefined, review_status: review || undefined, render_status: render || undefined,
    video_id: videoId ? Number(videoId) : undefined, project_id: projectId ? Number(projectId) : undefined,
    favorite: favorite ? true : undefined, sort,
  }), [q, review, render, videoId, projectId, favorite, sort]);
  const clips = useClips(filters);
  const videos = useVideos();
  const projects = useProjects();
  const filtered = Boolean(q || review || render || videoId || projectId || favorite);
  const options = useOptions();
  const jobs = useActiveJobs();
  const refresh = useRefreshAll();
  const toast = useToast();

  const items = clips.data?.items ?? [];
  const jobByClip = new Map((jobs.data ?? []).filter((j) => j.clip_id).map((j) => [j.clip_id as number, j]));
  const viewed = items.find((clip) => clip.id === viewing) ?? null;
  const chosen = items.filter((clip) => selected.has(clip.id));

  const toggle = (id: number) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const batch = async (action: string, confirm?: string) => {
    if (confirm && !window.confirm(confirm)) return;
    setBusy(true);
    try {
      const result = await api.post<{ changed: number }>("/clips/batch", { ids: [...selected], action });
      toast({ tone: "good", title: `${result.changed} clip${result.changed === 1 ? "" : "s"} updated` });
      if (action === "delete") setSelected(new Set());
      refresh();
    } catch (e) {
      toast({ tone: "bad", title: "That did not work", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const exportSelected = async () => {
    setBusy(true);
    try {
      const result = await api.post<{ folder: string; files: string[]; skipped: string[] }>("/clips/export", { ids: [...selected] });
      toast({
        tone: result.files.length ? "good" : "warn",
        title: result.files.length ? `${result.files.length} clip${result.files.length === 1 ? "" : "s"} exported` : "Nothing to export",
        body: result.files.length ? `Saved with their post text in ${result.folder}${result.skipped.length ? `. ${result.skipped.length} not rendered yet were skipped.` : ""}` : "Render the selected clips first.",
      });
    } catch (e) {
      toast({ tone: "bad", title: "Export failed", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-faint" />
          <Input value={q} onChange={(event) => setQ(event.target.value)} placeholder="Search titles and what was said…" className="pl-9" />
        </div>
        <Select value={projects.data?.some((p) => String(p.id) === projectId) ? projectId : ""} onChange={(event) => setProjectId(event.target.value)} className="w-auto! max-w-[200px]" aria-label="Project">
          <option value="">All projects</option>
          {projects.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </Select>
        <Button onClick={() => setManaging(true)} title="Create, rename or delete projects" aria-label="Manage projects"><Folders className="h-4 w-4" />Projects</Button>
        <Select value={videoId} onChange={(event) => setVideoId(event.target.value)} className="w-auto! max-w-[220px]" aria-label="Source video">
          <option value="">All videos</option>
          {videos.data?.map((video) => <option key={video.id} value={video.id}>{video.title}</option>)}
        </Select>
        <Select value={review} onChange={(event) => setReview(event.target.value)} className="w-auto! shrink-0" aria-label="Review status">
          <option value="">Any review status</option><option value="pending">Awaiting review</option><option value="approved">Approved</option><option value="rejected">Rejected</option>
        </Select>
        <Select value={render} onChange={(event) => setRender(event.target.value)} className="w-auto! shrink-0" aria-label="Render status">
          <option value="">Rendered or not</option><option value="ready">Rendered</option><option value="none">Not rendered</option><option value="failed">Render failed</option>
        </Select>
        <Select value={sort} onChange={(event) => setSort(event.target.value)} className="w-auto! shrink-0" aria-label="Sort by">
          <option value="score">Highest score</option><option value="rank">My ranking</option><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="duration">Longest</option><option value="source">By source video</option>
        </Select>
        <Button variant={favorite ? "good" : "secondary"} onClick={() => setFavorite((v) => !v)} aria-pressed={favorite}><Heart className={cx("h-4 w-4", favorite && "fill-current")} />Favourites</Button>
      </div>

      <div className="flex min-h-9 flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-3 py-1.5">
        <label className="flex items-center gap-2 text-[13px] text-muted">
          <input type="checkbox" className="h-4 w-4 accent-[var(--color-accent)]" checked={items.length > 0 && selected.size === items.length}
            onChange={(event) => setSelected(event.target.checked ? new Set(items.map((c) => c.id)) : new Set())} />
          {selected.size ? `${selected.size} selected` : `${clips.data?.total ?? 0} clip${clips.data?.total === 1 ? "" : "s"}`}
        </label>
        {selected.size > 0 && (
          <>
            <span className="flex-1" />
            <Button size="sm" onClick={() => batch("approve")} disabled={busy}><Check className="h-4 w-4" />Approve</Button>
            <Button size="sm" onClick={() => batch("reject")} disabled={busy}><X className="h-4 w-4" />Reject</Button>
            <Button size="sm" onClick={() => batch("render")} disabled={busy}><RefreshCw className="h-4 w-4" />Render</Button>
            <Button size="sm" onClick={exportSelected} disabled={busy} title="Copies the rendered clips and their post text into your export folder"><FolderOutput className="h-4 w-4" />Export to folder</Button>
            <Button size="sm" variant="danger" onClick={() => batch("delete", `Delete ${selected.size} clip${selected.size === 1 ? "" : "s"} and their rendered files? The source videos are kept.`)} disabled={busy}><Trash2 className="h-4 w-4" />Delete</Button>
          </>
        )}
      </div>

      {clips.isLoading && <div className="flex justify-center py-16"><Spinner className="h-6 w-6" /></div>}
      <ErrorNote error={clips.error} />
      {!clips.isLoading && items.length === 0 && (
        <Empty title={filtered ? "No clips match these filters" : "No clips yet"}>
          {filtered ? "Clear a filter to see more." : <>Generate clips in the <Link to="/clipper" className="text-accent-soft hover:underline">AI Clipper</Link>.</>}
        </Empty>
      )}

      <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
        {items.map((clip: Clip) => {
          const isSelected = selected.has(clip.id);
          return (
            <li key={clip.id} className={cx("group rounded-2xl border bg-panel p-2.5 transition-colors animate-rise", isSelected ? "border-accent/60" : "border-line hover:border-line-strong")}>
              <div className="relative">
                <ClipThumb clip={clip} job={jobByClip.get(clip.id)} onOpen={() => setViewing(clip.id)} className="w-full" />
                <label className="absolute left-2 top-2 flex h-6 w-6 cursor-pointer items-center justify-center rounded-md bg-black/60 backdrop-blur">
                  <input type="checkbox" aria-label={`Select ${clip.title}`} checked={isSelected} onChange={() => toggle(clip.id)} className="h-4 w-4 accent-[var(--color-accent)]" />
                </label>
                <div className="pointer-events-none absolute right-1.5 top-1.5 rounded-full bg-black/60 p-0.5 backdrop-blur"><ScoreRing score={clip.score} size={36} manual={clip.analysis_source === "manual"} /></div>
                {clip.favorite && <Heart className="absolute bottom-2 left-2 h-4 w-4 fill-accent text-accent drop-shadow" />}
              </div>
              <p className="mt-2 line-clamp-2 min-h-[2.4em] text-[13px] font-medium leading-snug text-ink" title={clip.title}>{clip.title}</p>
              <button type="button" className="mt-0.5 block max-w-full truncate text-left text-xs text-muted hover:text-accent-soft" title="Play the source from this moment"
                onClick={() => setPeek({ video: clip.video_id, time: clip.start })}>
                {clip.video_title} · {clock(clip.start)} · {duration(clip.end - clip.start)}
              </button>
              <div className="mt-1.5 flex flex-wrap gap-1">
                <ReviewBadge status={clip.review_status} />
                <QcBadge clip={clip} />
                {clip.origin === "variant" && <Badge tone="info">{clip.variant_label || "Variant"}</Badge>}
                {clip.permission_status === "unknown" || clip.permission_status === "denied" ? <RightsBadge status={clip.permission_status} /> : null}
              </div>
              <div className="mt-2 flex gap-1">
                <Link to={`/editor/${clip.id}`} className="flex-1"><Button size="sm" className="w-full justify-center"><Pencil className="h-3.5 w-3.5" />Edit</Button></Link>
                {clip.render_status === "ready" && <a href={`/api/clips/${clip.id}/file?download=1`}><Button size="icon" title="Download MP4" aria-label="Download"><Download className="h-4 w-4" /></Button></a>}
              </div>
            </li>
          );
        })}
      </ul>

      {selected.size > 0 && chosen.length !== selected.size && <p className="text-xs text-faint">Some selected clips are hidden by the current filters; actions still apply to them.</p>}
      <ClipViewer clip={viewed} options={options.data} onClose={() => setViewing(null)} onPeek={(time) => viewed && setPeek({ video: viewed.video_id, time })} />
      <SourcePeek videoId={peek?.video ?? null} time={peek?.time ?? null} onClose={() => setPeek(null)} />
      <ProjectsModal open={managing} onClose={() => setManaging(false)} />
    </div>
  );
}
