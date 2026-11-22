export function clock(seconds: number, withTenths = false): string {
  const safe = Math.max(0, seconds);
  const h = Math.floor(safe / 3600);
  const m = Math.floor((safe % 3600) / 60);
  const s = safe % 60;
  const secs = withTenths ? s.toFixed(1).padStart(4, "0") : String(Math.floor(s)).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${secs}` : `${m}:${secs}`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
  return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
}

export function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = value / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function ago(iso: string | null): string {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Score colour: the scale is an estimate, so three calm bands rather than a rainbow. */
export function scoreTone(score: number): "good" | "warn" | "muted" {
  return score >= 70 ? "good" : score >= 50 ? "warn" : "muted";
}

export function confidenceLabel(confidence: number): string {
  return confidence >= 0.75 ? "High confidence" : confidence >= 0.5 ? "Medium confidence" : "Low confidence";
}

export const PERMISSION_LABELS: Record<string, string> = {
  owned: "Your own content",
  licensed: "Licensed",
  permission: "Creator permission",
  unknown: "Rights not recorded",
  denied: "Not permitted",
};

export const FEEDBACK_LABELS: Record<string, string> = {
  good: "Good clip",
  bad: "Bad clip",
  wrong_moment: "Wrong moment",
  poor_subtitles: "Poor subtitles",
  bad_cropping: "Bad cropping",
  not_entertaining: "Not entertaining",
  too_much_context: "Needs too much context",
};
