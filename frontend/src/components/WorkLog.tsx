// The work log: after each job, one short line per thing done, saying who did it.
//   ChatGPT done this: …   Claude worked on this: …   Forge did this: …
import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { api, type Job } from "../lib/api";
import { ago, cx } from "../lib/format";
import { Card, CardHeader } from "./ui";

export type LogLine = { actor: "forge" | "claude" | "chatgpt" | string; text: string };

const WHO: Record<string, { label: string; className: string }> = {
  chatgpt: { label: "ChatGPT done this:", className: "text-good" },
  claude: { label: "Claude worked on this:", className: "text-accent-soft" },
  forge: { label: "Forge did this:", className: "text-info" },
};

const JOB_NAME: Record<string, string> = { analyze: "Finding clips", render: "Rendering", import: "Importing from a link", moment: "Your own moment", publish: "Posting", discover: "Checking followed channels" };

export function logOf(job: Job): LogLine[] {
  const log = (job.result as { log?: LogLine[] } | null)?.log;
  return Array.isArray(log) ? log : [];
}

export function WorkLogLines({ lines, className }: { lines: LogLine[]; className?: string }) {
  if (!lines.length) return null;
  return (
    <ul className={cx("space-y-1 text-[12.5px] leading-snug", className)}>
      {lines.map((line, index) => {
        const who = WHO[line.actor] ?? { label: `${line.actor}:`, className: "text-muted" };
        return (
          <li key={index}>
            <span className={cx("font-medium", who.className)}>{who.label}</span> <span className="text-ink">{line.text}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Every finished job of one video, newest first, each with its lines. */
export function VideoWorkLog({ videoId }: { videoId: number }) {
  const jobs = useQuery({
    queryKey: ["jobs", "video", videoId],
    queryFn: () => api.get<Job[]>(`/jobs?video_id=${videoId}&sort=recent&limit=60`),
    refetchInterval: 5000,
  });
  const done = (jobs.data ?? []).filter((job) => job.status !== "queued" && job.status !== "running" && logOf(job).length);
  if (!done.length) return null;
  return (
    <Card>
      <CardHeader title="Work log" icon={<ScrollText className="h-4 w-4 text-faint" />} hint="Who did what, after each job." />
      <ol className="space-y-4 px-5 pb-5">
        {done.map((job) => (
          <li key={job.id}>
            <p className="mb-1 text-xs text-faint">{JOB_NAME[job.type] ?? job.type} · {ago(job.finished_at ?? job.created_at)}</p>
            <WorkLogLines lines={logOf(job)} />
          </li>
        ))}
      </ol>
    </Card>
  );
}
