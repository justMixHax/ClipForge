import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, query } from "./api";
import type {
  Clip, ClipList, Dashboard, DiscoveryOverview, Feature, FoundVideo, HardwareReport, Job, LiveStats, NotificationItem, Options, Project, PromptEditResult,
  PublishOverview, PublishPost, SavedPreset, SettingsBody, Video,
} from "./api";

/** Refresh everything that can change when a job moves forward. */
export function useRefreshAll() {
  const client = useQueryClient();
  return () => {
    for (const key of ["videos", "video", "clips", "clip", "projects", "dashboard", "jobs", "notifications", "discovery", "discovery-videos"]) {
      client.invalidateQueries({ queryKey: [key] });
    }
  };
}

export function useOptions() {
  return useQuery({ queryKey: ["options"], queryFn: () => api.get<Options>("/options"), staleTime: 60_000 });
}

export function useVideos() {
  return useQuery({ queryKey: ["videos"], queryFn: () => api.get<Video[]>("/videos") });
}

export function useVideo(id: number | null) {
  return useQuery({
    queryKey: ["video", id],
    queryFn: () => api.get<Video>(`/videos/${id}`),
    enabled: id !== null,
  });
}

/** Your own presets for the AI Clipper's options. */
export function usePresets() {
  return useQuery({ queryKey: ["presets"], queryFn: () => api.get<SavedPreset[]>("/presets") });
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: () => api.get<Project[]>("/projects") });
}

export type ClipFilters = {
  video_id?: number | null;
  project_id?: number | null;
  review_status?: string;
  render_status?: string;
  favorite?: boolean;
  q?: string;
  min_score?: number;
  category?: string;
  sort?: string;
};

export function useClips(filters: ClipFilters, enabled = true) {
  return useQuery({
    queryKey: ["clips", filters],
    queryFn: () => api.get<ClipList>(`/clips${query(filters)}`),
    enabled,
  });
}

export function useClip(id: number | null) {
  return useQuery({ queryKey: ["clip", id], queryFn: () => api.get<Clip>(`/clips/${id}`), enabled: id !== null });
}

/** Active jobs, polled every second while anything is waiting or running, slowly otherwise. */
export function useActiveJobs() {
  return useQuery({
    queryKey: ["jobs", "active"],
    queryFn: () => api.get<Job[]>("/jobs?active=true"),
    refetchInterval: (state) => ((state.state.data?.length ?? 0) > 0 ? 1000 : 4000),
  });
}

export function useRecentJobs() {
  return useQuery({ queryKey: ["jobs", "recent"], queryFn: () => api.get<Job[]>("/jobs?limit=30"), refetchInterval: 5000 });
}

export function useDashboard() {
  return useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/dashboard"), refetchInterval: 8000 });
}

export function useStats() {
  return useQuery({ queryKey: ["stats"], queryFn: () => api.get<LiveStats>("/system/stats"), refetchInterval: 3000 });
}

export function useHardware() {
  return useQuery({ queryKey: ["hardware"], queryFn: () => api.get<HardwareReport>("/system/hardware"), staleTime: 60_000 });
}

export function useFeatures() {
  return useQuery({ queryKey: ["features"], queryFn: () => api.get<Feature[]>("/system/features"), staleTime: 30_000 });
}

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsBody>("/settings") });
}

export function useNotifications() {
  return useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<{ unread: number; items: NotificationItem[] }>("/notifications"),
    refetchInterval: 5000,
  });
}

export function usePatchClip() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api.patch<Clip>(`/clips/${id}`, body),
    onSuccess: (clip) => {
      client.setQueryData(["clip", clip.id], clip);
      client.invalidateQueries({ queryKey: ["clips"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

/** Describe a change in your own words: Claude makes it and the clip is rendered again. */
export function usePromptEdit() {
  const client = useQueryClient();
  const refresh = useRefreshAll();
  return useMutation({
    mutationFn: ({ id, instruction }: { id: number; instruction: string }) =>
      api.post<PromptEditResult>(`/clips/${id}/prompt-edit`, { instruction }),
    onSuccess: (result) => {
      client.setQueryData(["clip", result.clip.id], result.clip);
      refresh();
    },
  });
}

/** Put a clip back as it was before its last prompt edit (it is rendered again). */
export function useUndoPromptEdit() {
  const client = useQueryClient();
  const refresh = useRefreshAll();
  return useMutation({
    mutationFn: (id: number) => api.post<Clip>(`/clips/${id}/prompt-edit/undo`),
    onSuccess: (clip) => {
      client.setQueryData(["clip", clip.id], clip);
      refresh();
    },
  });
}

export type CoverBody = { text?: string; word?: string; time?: number; auto?: boolean };

/** Change a clip's thumbnail: its words, the word in colour or its moment. */
export function useRemakeCover() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: CoverBody }) => api.post<Clip>(`/clips/${id}/cover`, body),
    onSuccess: (clip) => {
      client.setQueryData(["clip", clip.id], clip);
      client.invalidateQueries({ queryKey: ["clips"] });
      client.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function usePublishing() {
  return useQuery({ queryKey: ["publishing"], queryFn: () => api.get<PublishOverview>("/publishing"), refetchInterval: 5000 });
}

const MOVING = new Set(["publishing", "processing"]);

export function usePosts() {
  return useQuery({
    queryKey: ["posts"],
    queryFn: () => api.get<PublishPost[]>("/publishing/posts"),
    refetchInterval: (state) => ((state.state.data ?? []).some((post) => MOVING.has(post.status)) ? 2000 : 8000),
  });
}

// ----------------------------------------------------------------------------------- creator discovery
export function useDiscovery() {
  return useQuery({
    queryKey: ["discovery"],
    queryFn: () => api.get<DiscoveryOverview>("/discovery"),
    refetchInterval: (state) => (state.state.data?.checking ? 1500 : 15000),
  });
}

export type FoundVideoFilters = { creator_id?: number; show: string; sort: string; min_minutes: number };

/** The uploads found on the channels you follow. Polled faster while one of them is being imported or analysed. */
export function useFoundVideos(filters: FoundVideoFilters, enabled = true) {
  return useQuery({
    queryKey: ["discovery-videos", filters],
    queryFn: () => api.get<FoundVideo[]>(`/discovery/videos${query(filters)}`),
    enabled,
    refetchInterval: (state) => ((state.state.data ?? []).some((v) => v.importing || v.library?.status === "analyzing") ? 2000 : 20000),
  });
}

/** The clipping options a video gets when nothing else is chosen: the defaults saved in Settings. */
export function analyzeDefaults(values: Record<string, unknown>): Record<string, unknown> {
  return {
    content_preset: String(values["clips.content_preset"] ?? "auto"), game_style: String(values["clips.game_style"] ?? ""),
    count: Number(values["clips.count"] ?? 5), length: String(values["clips.length"] ?? "15-60"),
    min_score: Number(values["clips.min_score"] ?? 55), aspect: String(values["clips.aspect"] ?? "9:16"),
    layout: String(values["clips.layout"] ?? "auto"), captions: String(values["clips.captions"] ?? "opus"),
    quality: String(values["processing.priority"] ?? "balanced"), auto_render: Boolean(values["clips.auto_render"] ?? true),
    show_hook: Boolean(values["clips.show_hook"] ?? true), remove_silence: false,
  };
}
