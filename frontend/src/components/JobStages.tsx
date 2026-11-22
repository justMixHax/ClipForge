import { Check, Circle, Loader2, SkipForward, X } from "lucide-react";
import type { Job } from "../lib/api";
import { api } from "../lib/api";
import { cx } from "../lib/format";
import { useRefreshAll } from "../lib/hooks";
import { Button, Progress, useToast } from "./ui";

/** Step-by-step view of a running job, with real progress from the worker. */
export function JobStages({ job, compact = false }: { job: Job; compact?: boolean }) {
  const refresh = useRefreshAll();
  const toast = useToast();
  const active = job.status === "queued" || job.status === "running";

  const cancel = async () => {
    try {
      await api.post(`/jobs/${job.id}/cancel`);
      refresh();
    } catch (error) {
      toast({ tone: "bad", title: "Could not cancel", body: (error as Error).message });
    }
  };
  const retry = async () => {
    try {
      await api.post(`/jobs/${job.id}/retry`);
      refresh();
    } catch (error) {
      toast({ tone: "bad", title: "Could not retry", body: (error as Error).message });
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink">
            {job.status === "queued" && "Waiting in the queue…"}
            {job.status === "running" && (job.message || "Working…")}
            {job.status === "succeeded" && "Finished"}
            {job.status === "failed" && "Failed"}
            {job.status === "cancelled" && "Cancelled"}
          </p>
          {job.status === "failed" && <p className="mt-0.5 break-words text-[13px] text-bad">{job.error}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="tabular text-[13px] text-muted">{Math.round(job.progress * 100)}%</span>
          {active && <Button size="sm" variant="ghost" onClick={cancel}>Cancel</Button>}
          {(job.status === "failed" || job.status === "cancelled") && <Button size="sm" onClick={retry}>Retry</Button>}
        </div>
      </div>
      <Progress value={job.progress} busy={job.status === "running"} tone={job.status === "failed" ? "bad" : job.status === "succeeded" ? "good" : "accent"} />
      {!compact && job.stages && (
        <ol className="space-y-1.5 pt-1">
          {job.stages.map((stage) => (
            <li key={stage.key} className="flex items-center gap-2.5 text-[13px]">
              <span className="flex h-4 w-4 items-center justify-center">
                {stage.status === "done" && <Check className="h-3.5 w-3.5 text-good" />}
                {stage.status === "skipped" && <SkipForward className="h-3.5 w-3.5 text-info" />}
                {stage.status === "running" && (job.status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" /> : <X className="h-3.5 w-3.5 text-bad" />)}
                {stage.status === "pending" && <Circle className="h-2 w-2 text-faint" />}
              </span>
              <span className={cx("flex-1 truncate", stage.status === "pending" ? "text-faint" : "text-ink")}>{stage.label}</span>
              {stage.note && <span className="truncate text-xs text-faint" title={stage.note}>{stage.note}</span>}
              {stage.status === "running" && job.status === "running" && <span className="tabular text-xs text-muted">{Math.round(stage.progress * 100)}%</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
