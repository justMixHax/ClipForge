// The typed client for the local ClipForge API. Every screen talks to the backend through this file.

export class ApiError extends Error {
  status: number;
  hint?: string;
  constructor(message: string, status: number, hint?: string) {
    super(message);
    this.status = status;
    this.hint = hint;
  }
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError("ClipForge is not responding. Is the app still running?", 0);
  }
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const info = (data ?? {}) as { error?: string; hint?: string };
    throw new ApiError(info.error ?? `Request failed (${response.status})`, response.status, info.hint);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** Upload a video file with progress. The file is sent as the raw body; the name travels in a header. */
export function uploadVideo(file: File, onProgress: (fraction: number) => void): { promise: Promise<Video>; cancel: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Video>((resolve, reject) => {
    xhr.open("POST", "/api/videos/upload");
    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let data: { error?: string; hint?: string } | Video | null = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300 && data) resolve(data as Video);
      else {
        const info = (data ?? {}) as { error?: string; hint?: string };
        reject(new ApiError(info.error ?? `Upload failed (${xhr.status})`, xhr.status, info.hint));
      }
    };
    xhr.onerror = () => reject(new ApiError("The upload was interrupted. Is the app still running?", 0));
    xhr.onabort = () => reject(new ApiError("Upload cancelled", 0));
    xhr.send(file);
  });
  return { promise, cancel: () => xhr.abort() };
}

// ---------------------------------------------------------------------------------------------- types
export interface Stage {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "skipped";
  progress: number;
  note: string;
}

export interface Job {
  id: number;
  type: "analyze" | "render" | "import" | "moment" | string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  priority: number;
  progress: number;
  stage: string;
  message: string;
  stages: Stage[] | null;
  error: string;
  video_id: number | null;
  clip_id: number | null;
  attempts: number;
  result: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type Permission = "owned" | "licensed" | "permission" | "unknown" | "denied";

export interface Video {
  id: number;
  project_id: number | null;
  title: string;
  original_filename: string;
  storage_mode: "managed" | "linked";
  size_bytes: number;
  duration: number;
  width: number;
  height: number;
  fps: number;
  video_codec: string;
  audio_codec: string;
  has_audio: boolean;
  source_type: string;
  source_url: string;
  creator_name: string;
  creator_handle: string;
  permission_status: Permission;
  permission_note: string;
  attribution: string;
  status: "ready" | "analyzing" | "analyzed" | "failed";
  language: string;
  content_preset: string;
  analysis: Record<string, unknown> | null;
  last_error: string;
  created_at: string;
  updated_at: string;
  stream_url: string;
  thumb_url: string;
  clip_count: number;
  active_job: Job | null;
  duplicate_of: number | null;
}

export interface Evidence {
  dimension: string;
  verdict?: "agree" | "disagree";
  impact: number;
  text: string;
  time: number | null;
  quote: string | null;
}

export interface QcCheck {
  key: string;
  label: string;
  status: "pass" | "warn" | "fail";
  detail: string;
}

export interface CaptionWord {
  text: string;
  start: number;
  end: number;
  emph?: boolean;
  locked?: boolean;
  hidden?: boolean;
  prob?: number;
}

export interface CaptionSettings {
  enabled?: boolean;
  preset?: string;
  font?: string;
  size?: number;
  color?: string;
  highlight?: string;
  emphasis?: string | null;
  uppercase?: boolean;
  max_words?: number;
  position?: number;
  censor?: boolean;
  outline?: number;
}

export interface ClipSettings {
  aspect: string;
  layout: string;
  subject_x: number | null;
  subject_y?: number | null;
  zoom: number;
  facecam_box: number[] | null;
  gameplay_box: number[] | null;
  captions: CaptionSettings;
  hook: { enabled?: boolean; seconds?: number };
  audio: { normalize?: boolean; denoise?: boolean };
  remove_silence: boolean;
  silence_threshold?: number;
  quality: string;
  fps: number;
  preset: string;
  game_style: string;
}

export interface Clip {
  id: number;
  video_id: number;
  project_id: number | null;
  origin: "ai" | "manual" | "variant";
  title: string;
  hook_text: string;
  hook_options: string[];
  description: string;
  hashtags: string[];
  category: string;
  transcript_text: string;
  start: number;
  end: number;
  score: number;
  subscores: Record<string, number>;
  confidence: number;
  explanation: string;
  evidence: Evidence[];
  analysis_source: string;
  rank: number;
  manual_rank: number | null;
  review_status: "pending" | "approved" | "rejected";
  favorite: boolean;
  settings: ClipSettings;
  captions: CaptionWord[] | null;
  render_status: "none" | "queued" | "rendering" | "ready" | "failed";
  render_error: string;
  file_size: number;
  out_width: number;
  out_height: number;
  out_duration: number;
  render_info: Record<string, unknown> | null;
  qc: QcCheck[] | null;
  rendered_at: string | null;
  variant_of: number | null;
  variant_label: string;
  created_at: string;
  updated_at: string;
  file_url: string;
  thumb_url: string;
  video_title: string;
  creator_name: string;
  permission_status: Permission | "";
  feedback: string[];
}

export interface ClipList {
  items: Clip[];
  total: number;
}

export interface Segment {
  id: number;
  idx: number;
  start: number;
  end: number;
  text: string;
  speaker: string;
  words: [string, number, number, number][];
  edited: boolean;
}

export interface Timeline {
  start: number;
  end: number;
  duration: number;
  envelope: number[];
  loud_events: { start: number; end: number; peak_db: number }[];
  cuts: number[];
  clips: { id: number; start: number; end: number; title: string; score: number; review_status: string }[];
  analysed: boolean;
}

export interface Preset<T = Record<string, unknown>> {
  key: string;
  label: string;
  description?: string;
  config?: T;
}

export interface WorkflowConfig {
  content_preset: string;
  count: number;
  length: string;
  min_score: number;
  aspect: string;
  layout: string;
  captions: string;
  quality: string;
  auto_render: boolean;
}

export interface CaptionPreset {
  key: string;
  label: string;
  style: {
    font: string;
    size: number;
    color: string;
    highlight: string;
    emphasis: string | null;
    uppercase: boolean;
    max_words: number;
    animation: string;
    box: boolean;
    outline: number;
    position: number;
  };
}

export interface SpeechModel {
  key: string;
  label: string;
  size_mb: number;
  multilingual: boolean;
  note: string;
  installed: boolean;
  on_disk_elsewhere: boolean;
  install: { state: string; message: string } | null;
}

export interface Options {
  content_presets: (Preset & { captions: string; layout: string })[];
  workflow_presets: Preset<WorkflowConfig>[];
  game_styles: { key: string; label: string }[];
  length_ranges: { key: string; min: number; max: number; label: string }[];
  aspects: { key: string; width: number; height: number }[];
  layouts: { key: string; label: string }[];
  caption_presets: CaptionPreset[];
  fonts: { name: string; source: string }[];
  dimensions: { key: string; label: string }[];
  categories: Record<string, string>;
  feedback_labels: string[];
  speech_models: SpeechModel[];
  max_upload_gb: number;
}

export interface LinkInfo {
  url: string;
  title: string;
  creator: string;
  creator_url: string;
  site: string;
  video_id: string;
  duration: number | null;
  is_live: boolean;
  license: string;
  approx_bytes: number | null;
  width: number | null;
  height: number | null;
  over_limit: boolean;
  limit_gb: number;
  existing_video_id: number | null;
}

export interface Project {
  id: number;
  name: string;
  description: string;
  created_at: string;
  video_count: number;
  clip_count: number;
}

export interface Feature {
  key: string;
  area: string;
  name: string;
  status: "working" | "needs_setup" | "unavailable" | "blocked" | "planned";
  detail: string;
}

export interface Hardware {
  detected_at: string;
  cpu: { name: string; cores: number; threads: number };
  memory: { total_gb: number; available_gb: number; used_percent: number };
  gpus: { name: string; memory_total_mb: number | null; driver: string }[];
  ffmpeg: string;
  encoders: Record<string, { ok: boolean; detail: string }>;
  whisper_cuda: { ok: boolean; detail: string };
  face_detector: boolean;
  storage: { path: string; free_gb: number; total_gb: number };
}

export interface HardwareReport {
  hardware: Hardware;
  recommended: {
    priority: string;
    video_encoder: string;
    whisper_device: string;
    whisper_compute: string;
    whisper_model: string | null;
    notes: string[];
  };
}

export interface LiveStats {
  cpu_percent: number;
  memory: { total_gb: number; available_gb: number; used_percent: number };
  gpu: { name: string; memory_total_mb: number | null; memory_used_mb: number | null; utilization_percent: number | null; temperature_c: number | null } | null;
  queue_length: number;
  running: Job | null;
}

export interface StorageUsage {
  areas: Record<string, number>;
  total: number;
  disk_free: number;
  disk_total: number;
  data_dir: string;
}

export interface Dashboard {
  videos: { total: number; analyzed: number; hours: number };
  clips: { total: number; awaiting_review: number; approved: number; rejected: number; rendered: number; favorites: number; average_score: number };
  publishing: { connected_accounts: number; scheduled: number; published: number; in_drafts: number; failed: number; verified_views: number | null; note: string };
  queue: { waiting: number; running: number; failed_this_week: number };
  compute: { analyze_seconds_7d: number; render_seconds_7d: number; jobs_7d: number };
  top_clips: Clip[];
  recent_jobs: Job[];
  storage: StorageUsage;
}

export interface SettingsBody {
  values: Record<string, unknown>;
  secrets: Record<string, boolean>;
  feedback: { count: number; counts: Record<string, number>; effects: string[] };
}

export interface NotificationItem {
  id: number;
  kind: string;
  title: string;
  body: string;
  link: string;
  read: boolean;
  created_at: string;
}

export interface SearchHit {
  segment_id: number;
  video_id: number;
  video_title: string;
  start: number;
  end: number;
  text: string;
  clip_start: number;
  clip_end: number;
}

// -------------------------------------------------------------------------------------------- publishing
export type PlatformKey = "youtube" | "tiktok" | "instagram";
export type PostStatus = "scheduled" | "publishing" | "processing" | "published" | "scheduled_on_platform" | "private" | "in_drafts" | "failed" | "cancelled";

export interface PublishPlatform {
  platform: PlatformKey;
  label: string;
  connect: "sign_in" | "token";
  redirect_uri: string;
  configured: boolean;
  client_id: string;
}

export interface PublishAccount {
  id: number;
  platform: PlatformKey;
  label: string;
  name: string;
  handle: string;
  status: "connected" | "needs_reconnect";
  detail: string;
  reconnect_by: string | null;
  connected_at: string;
  extra: { page?: string; avatar_url?: string };
}

export interface PublishOverview {
  platforms: PublishPlatform[];
  accounts: PublishAccount[];
  daily_limit: number;
}

export interface PublishPost {
  id: number;
  clip_id: number | null;
  account_id: number | null;
  platform: PlatformKey;
  label: string;
  status: PostStatus;
  scheduled_at: string | null;
  next_run_at: string | null;
  title: string;
  caption: string;
  hashtags: string[];
  privacy: string;
  options: Record<string, unknown>;
  external_id: string;
  url: string;
  detail: string;
  error: string;
  attempts: number;
  clip_title: string;
  account_name: string;
  created_at: string;
  published_at: string | null;
  thumb_url: string;
}
