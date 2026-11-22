import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, query } from "./api";
import type {
  Clip, ClipList, Dashboard, Feature, HardwareReport, Job, LiveStats, NotificationItem, Options, Project, PublishOverview,
  PublishPost, SettingsBody, Video,
} from "./api";

/** Refresh everything that can change when a job moves forward. */
export function useRefreshAll() {
  const client = useQueryClient();
  return () => {
    for (const key of ["videos", "video", "clips", "clip", "projects", "dashboard", "jobs", "notifications"]) {
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
