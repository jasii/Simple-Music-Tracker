import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api";
import type { DownloadJob } from "../types";
import { timeAgo } from "../lib/format";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Progress } from "./ui/progress";

// What each job status reads as, and how loud its badge is.
const STATUS: Record<string, { label: string; tone: string }> = {
  searching: { label: "Searching", tone: "text-muted-foreground" },
  downloading: { label: "Downloading", tone: "text-sky-500 border-sky-600/40" },
  complete: { label: "Complete", tone: "text-emerald-500 border-emerald-600/40" },
  partial: { label: "Incomplete", tone: "text-amber-500 border-amber-600/40" },
  failed: { label: "Failed", tone: "text-destructive border-destructive/40" },
  cancelled: { label: "Cancelled", tone: "text-muted-foreground" },
};

const FILE_STATE: Record<string, string> = {
  pending: "requested",
  queued: "in their queue",
  downloading: "downloading",
  done: "done",
  failed: "failed",
  gave_up: "gave up",
};

const ACTIVE = ["searching", "downloading"];

function albumHref(j: DownloadJob): string {
  const qs = new URLSearchParams({ artist: j.artist, title: j.title, from: "missing" });
  return "/album?" + qs.toString();
}

/**
 * Soulseek downloads, followed file by file. A Soulseek album is a request per
 * file to someone's home connection, so this is where "7 of 9 landed" shows up -- with the
 * buttons to ask again, ask someone else, or stop.
 */
export function DownloadsPanel() {
  const qc = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: ["downloads"],
    queryFn: () => api.downloads(),
    // Quick while something is moving, lazy once everything has settled.
    refetchInterval: (q) =>
      q.state.data?.jobs.some((j) => ACTIVE.includes(j.status)) ? 5000 : 30000,
  });
  const jobs = data?.jobs ?? [];
  const finished = jobs.filter((j) => j.status === "complete" || j.status === "cancelled").length;

  function clear() {
    api.downloadsClear().then((r) => {
      toast.success(`Cleared ${r.removed} finished download${r.removed === 1 ? "" : "s"}.`);
      qc.invalidateQueries({ queryKey: ["downloads"] });
    });
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {isPending ? "Loading..." : `${jobs.length} download${jobs.length === 1 ? "" : "s"}`}
        </p>
        {finished > 0 && (
          <Button size="xs" variant="outline" onClick={clear}>
            Clear finished
          </Button>
        )}
      </div>
      <div className="divide-y">
        {jobs.map((j) => (
          <JobRow key={j.id} job={j} />
        ))}
      </div>
      {!isPending && !jobs.length && (
        <p className="text-muted-foreground">
          Nothing yet. Albums grabbed through slskd show up here, file by file, until
          every track has landed or been given up on.
        </p>
      )}
    </div>
  );
}

function JobRow({ job }: { job: DownloadJob }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const status = STATUS[job.status] ?? { label: job.status, tone: "" };
  const active = ACTIVE.includes(job.status);
  const shortfall = job.failed > 0 || job.status === "partial" || job.status === "failed";
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;

  function run(action: "retry" | "another-peer" | "cancel") {
    setBusy(action);
    api
      .downloadAction(job.id, action)
      .then((r) => {
        if (r.error) toast.error(r.error);
        else if (r.message) toast.success(`${job.title}: ${r.message}`);
        qc.invalidateQueries({ queryKey: ["downloads"] });
      })
      .catch(() => toast.error("That didn't go through."))
      .finally(() => setBusy(null));
  }

  function dismiss() {
    api.downloadDelete(job.id).then(() => qc.invalidateQueries({ queryKey: ["downloads"] }));
  }

  return (
    <div className="py-2.5">
      <div className="flex flex-col gap-x-3 gap-y-1.5 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-semibold">
            <RouterLink to={albumHref(job)} className="hover:underline">{job.title}</RouterLink>
            <Badge variant="outline" className={status.tone}>{status.label}</Badge>
            {job.kind === "tracks" && (
              <Badge variant="outline" className="text-muted-foreground">Missing tracks</Badge>
            )}
          </p>
          <p className="text-sm text-muted-foreground">
            {job.artist_id ? (
              <RouterLink to={`/artist/${job.artist_id}`} className="hover:underline">{job.artist}</RouterLink>
            ) : (
              job.artist
            )}
            {job.format ? ` · ${job.format.toUpperCase()}` : ""}
            {` · ${timeAgo(job.created_at)}`}
            {job.message ? ` · ${job.message}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {shortfall && (
            <Button size="xs" variant="outline" disabled={!!busy} onClick={() => run("retry")}>
              {busy === "retry" ? "Retrying..." : "Retry failed"}
            </Button>
          )}
          {(active || shortfall) && job.status !== "searching" && (
            <Button size="xs" variant="outline" disabled={!!busy} onClick={() => run("another-peer")}>
              {busy === "another-peer" ? "Looking..." : "Try another peer"}
            </Button>
          )}
          {active && (
            <Button size="xs" variant="ghost" disabled={!!busy} onClick={() => run("cancel")}>
              Cancel
            </Button>
          )}
          {!active && (
            <Button size="xs" variant="ghost" onClick={dismiss}>
              Dismiss
            </Button>
          )}
        </div>
      </div>
      {job.total > 0 && (
        <div className="mt-1.5 flex items-center gap-2 text-sm text-muted-foreground">
          <Progress value={pct} className="w-32" />
          <span>
            {job.done}/{job.total} files
            {job.failed ? ` · ${job.failed} failed` : ""}
          </span>
          <Button size="xs" variant="ghost" onClick={() => setOpen((v) => !v)}>
            {open ? "Hide files" : "Show files"}
          </Button>
        </div>
      )}
      {open && (
        <ul className="mt-1.5 space-y-0.5 text-sm">
          {job.files.map((f, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-x-2">
              <span className="min-w-0 truncate">{f.name}</span>
              <span
                className={
                  f.state === "done"
                    ? "text-emerald-500"
                    : f.state === "failed" || f.state === "gave_up"
                      ? "text-destructive"
                      : "text-muted-foreground"
                }
              >
                {FILE_STATE[f.state] ?? f.state}
                {f.state === "downloading" && f.percent ? ` ${f.percent}%` : ""}
              </span>
              <span className="text-muted-foreground">
                from {f.username}
                {f.attempts > 0 ? ` · try ${f.attempts + 1}` : ""}
                {f.error && f.state !== "done" ? ` · ${f.error}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
