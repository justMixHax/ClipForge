// The editor's timeline: loudness waveform, the clip's in and out handles, word ticks, loud moments,
// scene cuts and the playhead. Handles snap to the gaps between words so cuts do not land mid-word.
import { useEffect, useRef, useState } from "react";
import { clock } from "../lib/format";

export type TimedWord = { text: string; start: number; end: number };

type Props = {
  from: number;              // source time at the left edge
  to: number;                // source time at the right edge
  start: number;
  end: number;
  time: number;
  envelope: number[];
  loud: { start: number; end: number; peak_db: number }[];
  cuts: number[];
  /** Parts left out of the clip, drawn as darker striped bands. */
  leftOut?: [number, number][];
  words: TimedWord[];
  snap: boolean;
  onSeek: (time: number) => void;
  onDragStart: () => void;
  onRange: (start: number, end: number) => void;
};

/** The nearest safe cut point to t: just before a word starts (in handle) or just after one ends (out handle). */
export function snapTime(t: number, words: TimedWord[], edge: "start" | "end", reach = 0.35): number {
  let best = t;
  let distance = reach;
  for (let i = 0; i < words.length; i += 1) {
    const candidate = edge === "start"
      ? Math.max(words[i].start - 0.12, i > 0 ? words[i - 1].end + 0.02 : 0)
      : Math.min(words[i].end + 0.25, i + 1 < words.length ? words[i + 1].start - 0.03 : Infinity);
    const d = Math.abs(candidate - t);
    if (d < distance) {
      distance = d;
      best = candidate;
    }
  }
  return best;
}

export function Timeline({ from, to, start, end, time, envelope, loud, cuts, leftOut = [], words, snap, onSeek, onDragStart, onRange }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const drag = useRef<"start" | "end" | null>(null);
  const span = Math.max(to - from, 0.001);
  const x = (t: number) => ((t - from) / span) * width;
  const t = (px: number) => from + (Math.min(Math.max(px, 0), width) / Math.max(width, 1)) * span;

  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver((entries) => setWidth(entries[0].contentRect.width));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);

  // Draw the waveform: muted everywhere, accent inside the selected range.
  useEffect(() => {
    const element = canvas.current;
    if (!element || width === 0) return;
    const height = 96;
    const ratio = window.devicePixelRatio || 1;
    element.width = Math.round(width * ratio);
    element.height = Math.round(height * ratio);
    const context = element.getContext("2d");
    if (!context) return;
    context.scale(ratio, ratio);
    context.clearRect(0, 0, width, height);
    const count = envelope.length;
    if (count === 0) return;
    const bar = width / count;
    for (let i = 0; i < count; i += 1) {
      const at = from + ((i + 0.5) / count) * span;
      const inside = at >= start && at <= end;
      const h = Math.max(2, envelope[i] * (height - 14));
      context.fillStyle = inside ? "rgba(255,122,61,0.85)" : "rgba(105,113,126,0.55)";
      context.fillRect(i * bar, (height - h) / 2, Math.max(1, bar - 0.6), h);
    }
  }, [envelope, width, from, span, start, end]);

  const pointerTime = (event: React.PointerEvent) => t(event.clientX - (box.current?.getBoundingClientRect().left ?? 0));

  const moveHandle = (event: React.PointerEvent) => {
    if (!drag.current) return;
    let value = pointerTime(event);
    if (snap && !event.altKey) value = snapTime(value, words, drag.current);
    if (drag.current === "start") onRange(Math.min(value, end - 0.5), end);
    else onRange(start, Math.max(value, start + 0.5));
  };
  const grab = (edge: "start" | "end") => (event: React.PointerEvent) => {
    event.stopPropagation();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    drag.current = edge;
    onDragStart();
  };
  const release = (event: React.PointerEvent) => {
    if (drag.current) (event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId);
    drag.current = null;
  };
  const nudge = (edge: "start" | "end") => (event: React.KeyboardEvent) => {
    const step = event.key === "ArrowLeft" ? -0.1 : event.key === "ArrowRight" ? 0.1 : 0;
    if (!step) return;
    event.preventDefault();
    onDragStart();
    if (edge === "start") onRange(Math.min(Math.max(from, start + step), end - 0.5), end);
    else onRange(start, Math.max(Math.min(to, end + step), start + 0.5));
  };

  const handle = "absolute top-0 z-10 h-full w-3 -translate-x-1/2 cursor-ew-resize touch-none focus-visible:outline-2";
  return (
    <div className="select-none">
      <div ref={box} className="relative h-24 cursor-pointer overflow-hidden rounded-xl border border-line bg-bg"
        onPointerDown={(event) => { if (!drag.current) onSeek(pointerTime(event)); }}>
        <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
        {loud.map((event) => (
          <span key={event.start} title={`Gets loud here (+${event.peak_db.toFixed(0)} dB)`} className="absolute top-0 h-1 rounded-b bg-warn/80"
            style={{ left: x(event.start), width: Math.max(3, x(event.end) - x(event.start)) }} />
        ))}
        {cuts.map((cut) => <span key={cut} title="Scene change" className="absolute top-0 h-full border-l border-dashed border-info/50" style={{ left: x(cut) }} />)}
        {words.map((word) => <span key={word.start} className="absolute bottom-0 h-1.5 bg-ink/30" style={{ left: x(word.start), width: Math.max(1, x(word.end) - x(word.start) - 1) }} />)}
        <span className="pointer-events-none absolute top-0 h-full bg-black/45" style={{ left: 0, width: Math.max(0, x(start)) }} />
        <span className="pointer-events-none absolute top-0 h-full bg-black/45" style={{ left: x(end), right: 0 }} />
        <span className="pointer-events-none absolute top-0 h-full border-y-2 border-accent" style={{ left: x(start), width: Math.max(0, x(end) - x(start)) }} />
        {leftOut.map(([a, b]) => (
          <span key={`${a}-${b}`} title={`Left out: ${clock(a, true)} – ${clock(b, true)}`} className="absolute top-0 h-full bg-black/55"
            style={{ left: x(a), width: Math.max(2, x(b) - x(a)), backgroundImage: "repeating-linear-gradient(135deg, transparent 0 6px, rgba(255,255,255,0.09) 6px 8px)" }} />
        ))}
        <span role="slider" tabIndex={0} aria-label="Clip start" aria-valuenow={Number(start.toFixed(2))} className={handle} style={{ left: x(start) }}
          onPointerDown={grab("start")} onPointerMove={moveHandle} onPointerUp={release} onKeyDown={nudge("start")}>
          <span className="absolute left-1/2 top-0 h-full w-1.5 -translate-x-1/2 rounded bg-accent" />
        </span>
        <span role="slider" tabIndex={0} aria-label="Clip end" aria-valuenow={Number(end.toFixed(2))} className={handle} style={{ left: x(end) }}
          onPointerDown={grab("end")} onPointerMove={moveHandle} onPointerUp={release} onKeyDown={nudge("end")}>
          <span className="absolute left-1/2 top-0 h-full w-1.5 -translate-x-1/2 rounded bg-accent" />
        </span>
        <span className="pointer-events-none absolute top-0 z-20 h-full w-px bg-white" style={{ left: x(time) }} />
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular text-faint">
        <span>{clock(from, true)}</span>
        <span className="text-muted">drag the orange handles to trim · click to move the playhead · hold Alt to place a handle freely</span>
        <span>{clock(to, true)}</span>
      </div>
    </div>
  );
}
