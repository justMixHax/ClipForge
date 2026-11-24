// Choose two parts of the picture for the stacked layouts: what goes on top and what goes below (for example two
// cameras in a recorded video call, or a facecam over the gameplay). Drawn on a large frame of the source video.
import { useEffect, useRef, useState } from "react";
import type { Video } from "../lib/api";
import { clock, cx } from "../lib/format";
import { Button, Modal, Segmented } from "./ui";

export type Part = number[];  // x, y, width, height in pixels of the source video
type Which = "top" | "bottom";
const SMALLEST = 0.05;  // a part covers at least this share of the picture each way (as the server checks)

/** In words: how much of the picture a part covers and where it starts. */
export function describePart(part: Part, video: Pick<Video, "width" | "height">): string {
  const [x, y, w, h] = part;
  const pct = (value: number, size: number) => Math.round((value / size) * 100);
  return `${pct(w, video.width)}% × ${pct(h, video.height)}%, from ${pct(x, video.width)}% across, ${pct(y, video.height)}% down`;
}

function Outline({ part, video, label, tone, dashed = false }: { part: Part; video: Pick<Video, "width" | "height">; label: string; tone: "accent" | "info"; dashed?: boolean }) {
  const [x, y, w, h] = part;
  return (
    <div className={cx("pointer-events-none absolute border-2", dashed && "border-dashed", tone === "accent" ? "border-accent bg-accent/10" : "border-info bg-info/10")}
      style={{ left: `${(x / video.width) * 100}%`, top: `${(y / video.height) * 100}%`, width: `${(w / video.width) * 100}%`, height: `${(h / video.height) * 100}%` }}>
      <span className={cx("absolute left-0 top-0 rounded-br px-1.5 py-0.5 text-[11px] font-semibold text-black", tone === "accent" ? "bg-accent" : "bg-info")}>{label}</span>
    </div>
  );
}

export function PartsPicker({ open, onClose, video, time, start, end, top, bottom, onUse, topLabel = "Top", bottomLabel = "Bottom" }: {
  open: boolean; onClose: () => void; video: Video; time: number; start: number; end: number; top: Part | null; bottom: Part | null;
  onUse: (top: Part, bottom: Part) => void; topLabel?: string; bottomLabel?: string;
}) {
  // The frame to draw on: the playhead's, unless it sits at an end of the clip (where a call often shows one camera
  // only), then the middle. A slider moves to any moment of the clip.
  const first = time > start + 0.5 && time < end - 0.5 ? time : (start + end) / 2;
  const [at, setAt] = useState(first);
  const [parts, setParts] = useState<Record<Which, Part | null>>({ top, bottom });
  const [which, setWhich] = useState<Which>("top");
  const [drawing, setDrawing] = useState<Part | null>(null);
  const origin = useRef<[number, number] | null>(null);  // where the box being drawn began
  const area = useRef<HTMLDivElement>(null);
  const player = useRef<HTMLVideoElement>(null);
  useEffect(() => {  // each time it opens: the parts as saved, and the first moment to draw on
    if (!open) return;
    setParts({ top, bottom });
    setWhich(top && !bottom ? "bottom" : "top");
    setDrawing(null);
    setAt(first);
  }, [open, top, bottom]);
  useEffect(() => {  // show the chosen moment
    if (open && player.current && player.current.readyState >= 1) player.current.currentTime = at;
  }, [open, at]);

  const point = (event: React.PointerEvent): [number, number] => {
    const rect = area.current!.getBoundingClientRect();
    const fx = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const fy = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    return [fx * video.width, fy * video.height];
  };
  const box = (a: [number, number], b: [number, number]): Part =>
    [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1])].map(Math.round);
  const down = (event: React.PointerEvent<HTMLDivElement>) => {
    origin.current = point(event);
    setDrawing(box(origin.current, origin.current));
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const move = (event: React.PointerEvent) => { if (origin.current) setDrawing(box(origin.current, point(event))); };
  const up = (event: React.PointerEvent) => {
    if (!origin.current) return;
    const part = box(origin.current, point(event));
    origin.current = null;
    setDrawing(null);
    if (part[2] < video.width * SMALLEST || part[3] < video.height * SMALLEST) return;  // a click, not a box
    const next = { ...parts, [which]: part };
    setParts(next);
    if (which === "top" && !next.bottom) setWhich("bottom");
  };
  const ready = Boolean(parts.top && parts.bottom);

  return (
    <Modal open={open} onClose={onClose} title="Choose the two parts" wide
      footer={(
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready} onClick={() => { if (parts.top && parts.bottom) { onUse(parts.top, parts.bottom); onClose(); } }}>Use these parts</Button>
        </>
      )}>
      <div className="space-y-3">
        <p className="text-[13px] text-muted">Drag a box round what goes on top, then round what goes below, for example the two cameras of a video call. Each box can be drawn again. The clip shows the {topLabel.toLowerCase()} part above the {bottomLabel.toLowerCase()} one.</p>
        <Segmented value={which} onChange={setWhich} options={[
          { value: "top", label: `Drawing: ${topLabel.toLowerCase()} part${parts.top ? " ✓" : ""}` },
          { value: "bottom", label: `Drawing: ${bottomLabel.toLowerCase()} part${parts.bottom ? " ✓" : ""}` },
        ]} />
        <div ref={area} className="relative w-full cursor-crosshair touch-none select-none overflow-hidden rounded-xl border border-line bg-black"
          style={{ aspectRatio: `${video.width || 16} / ${video.height || 9}` }} onPointerDown={down} onPointerMove={move} onPointerUp={up}>
          <video ref={player} src={video.stream_url} muted playsInline preload="auto" className="pointer-events-none absolute inset-0 h-full w-full"
            onLoadedMetadata={(event) => { event.currentTarget.currentTime = at; }} />
          {parts.top && <Outline part={parts.top} video={video} label={topLabel} tone="accent" />}
          {parts.bottom && <Outline part={parts.bottom} video={video} label={bottomLabel} tone="info" />}
          {drawing && <Outline part={drawing} video={video} label={which === "top" ? topLabel : bottomLabel} tone={which === "top" ? "accent" : "info"} dashed />}
        </div>
        <label className="flex items-center gap-3 text-xs text-muted">
          <span className="w-28 shrink-0 tabular">Frame at {clock(Math.max(0, at - start), true)}</span>
          <input type="range" min={start} max={end} step={0.5} value={at} aria-label="Moment of the clip to draw on"
            onChange={(event) => setAt(Number(event.target.value))} className="w-full" />
        </label>
        <p className="text-xs text-faint">
          {parts.top ? `${topLabel}: ${describePart(parts.top, video)}.` : `${topLabel}: not drawn yet.`}{" "}
          {parts.bottom ? `${bottomLabel}: ${describePart(parts.bottom, video)}.` : `${bottomLabel}: not drawn yet.`}
        </p>
      </div>
    </Modal>
  );
}
