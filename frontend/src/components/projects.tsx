// Projects group videos and their clips (a series, a client, a campaign). A video's clips always live in
// the same project as the video.
import { useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Project } from "../lib/api";
import { useProjects } from "../lib/hooks";
import { Button, Empty, ErrorNote, Input, Modal, Select, Spinner, useToast } from "./ui";

const NEW = "__new__";

function useProjectChanges() {
  const client = useQueryClient();
  return () => {
    for (const key of ["projects", "videos", "video", "clips", "clip"]) client.invalidateQueries({ queryKey: [key] });
  };
}

/** Pick the project a video belongs to, or make a new one on the spot. */
export function ProjectSelect({ value, onChange }: { value: number | null; onChange: (projectId: number | null) => void }) {
  const projects = useProjects();
  const changed = useProjectChanges();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const project = await api.post<Project>("/projects", { name });
      changed();
      onChange(project.id);
      setCreating(false);
      setName("");
    } catch (e) {
      toast({ tone: "bad", title: "Could not create the project", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  if (creating) {
    return (
      <div className="flex gap-2">
        <Input autoFocus value={name} maxLength={200} placeholder="Project name" aria-label="New project name"
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") create(); if (event.key === "Escape") setCreating(false); }} />
        <Button variant="primary" onClick={create} busy={busy} disabled={!name.trim()}>Create</Button>
        <Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
      </div>
    );
  }
  return (
    <Select value={value ?? ""} aria-label="Project"
      onChange={(event) => (event.target.value === NEW ? setCreating(true) : onChange(event.target.value ? Number(event.target.value) : null))}>
      <option value="">No project</option>
      {projects.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
      <option value={NEW}>+ New project…</option>
    </Select>
  );
}

function ProjectRow({ project }: { project: Project }) {
  const changed = useProjectChanges();
  const toast = useToast();
  const [name, setName] = useState(project.name);
  useEffect(() => setName(project.name), [project.name]);

  const rename = async () => {
    if (!name.trim() || name.trim() === project.name) { setName(project.name); return; }
    try {
      await api.patch<Project>(`/projects/${project.id}`, { name });
      changed();
    } catch (e) {
      setName(project.name);
      toast({ tone: "bad", title: "Could not rename the project", body: (e as Error).message });
    }
  };
  const remove = async () => {
    const holds = project.video_count ? ` Its ${project.video_count} video${project.video_count === 1 ? "" : "s"} and ${project.clip_count} clip${project.clip_count === 1 ? "" : "s"} are kept and become unassigned.` : "";
    if (!window.confirm(`Delete the project “${project.name}”?${holds}`)) return;
    try {
      await api.del(`/projects/${project.id}`);
      changed();
    } catch (e) {
      toast({ tone: "bad", title: "Could not delete the project", body: (e as Error).message });
    }
  };

  return (
    <li className="flex items-center gap-2 py-2">
      <Input value={name} maxLength={200} aria-label={`Name of project ${project.name}`} onChange={(event) => setName(event.target.value)}
        onBlur={rename} onKeyDown={(event) => event.key === "Enter" && (event.target as HTMLInputElement).blur()} />
      <span className="w-40 shrink-0 text-right text-xs text-muted tabular-nums">
        {project.video_count} video{project.video_count === 1 ? "" : "s"} · {project.clip_count} clip{project.clip_count === 1 ? "" : "s"}
      </span>
      <Button variant="ghost" size="icon" onClick={remove} title="Delete this project (videos and clips are kept)" aria-label={`Delete project ${project.name}`}><Trash2 className="h-4 w-4" /></Button>
    </li>
  );
}

/** Create, rename and delete projects. */
export function ProjectsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const projects = useProjects();
  const changed = useProjectChanges();
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.post<Project>("/projects", { name });
      setName("");
      changed();
    } catch (e) {
      toast({ tone: "bad", title: "Could not create the project", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Projects">
      <div className="space-y-3 p-5">
        <p className="text-[13px] text-muted">
          Group videos and their clips by series, client or campaign. Put a video in a project from the AI Clipper, under “Source and rights”.
        </p>
        <div className="flex gap-2">
          <Input value={name} maxLength={200} placeholder="New project name" aria-label="New project name"
            onChange={(event) => setName(event.target.value)} onKeyDown={(event) => event.key === "Enter" && create()} />
          <Button variant="primary" onClick={create} busy={busy} disabled={!name.trim()}><FolderPlus className="h-4 w-4" />Create</Button>
        </div>
        <ErrorNote error={projects.error} />
        {projects.isLoading ? <div className="flex justify-center py-6"><Spinner /></div>
          : projects.data?.length ? <ul className="divide-y divide-line">{projects.data.map((project) => <ProjectRow key={project.id} project={project} />)}</ul>
          : <Empty title="No projects yet">Create one above. Videos without a project stay in the library as they are.</Empty>}
      </div>
    </Modal>
  );
}
