// A clip's thumbnail (the picture people see before they tap) and the caption to paste into TikTok.
import { Check, Copy, Crosshair, Download, ImageIcon, RefreshCw, RotateCcw, Save, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import type { Clip } from "../lib/api";
import { api } from "../lib/api";
import { clock, cx } from "../lib/format";
import { usePatchClip, useRemakeCover, type CoverBody } from "../lib/hooks";
import { Button, Field, Input, Segmented, Spinner, Textarea, useToast } from "./ui";

/** A word as the server compares it: without the punctuation around it ($, %, ' and - stay). */
const bare = (word: string) => word.replace(/[^\p{L}\p{N}\p{M}_$%'-]/gu, "");

/** The source time shown at a moment of the rendered clip (pauses cut by silence removal are skipped). */
export function sourceTime(clip: Clip, clipSeconds: number): number {
  const segments = (clip.render_info?.segments as [number, number][] | undefined) ?? [[clip.start, clip.end]];
  let left = Math.max(0, clipSeconds);
  for (const [a, b] of segments) {
    if (left <= b - a) return a + left;
    left -= b - a;
  }
  return clip.end;
}

export function ThumbnailPanel({ clip, frameTime }: {
  clip: Clip;
  /** The source time of the frame on screen, for “Use the frame on screen”. */
  frameTime?: () => number | null;
}) {
  const remake = useRemakeCover();
  const toast = useToast();
  const [shown, setShown] = useState(clip);  // the latest answer, so the picture changes at once
  const [text, setText] = useState(clip.cover_text);
  const [word, setWord] = useState(clip.cover_word);
  const [at, setAt] = useState<number | null>(clip.cover_time);
  const [pending, setPending] = useState<"make" | "best" | null>(null);
  useEffect(() => setShown(clip), [clip]);
  // What you are typing stays while the clip updates (a render moving along, a refresh); only words or a moment
  // saved anew replace it.
  useEffect(() => {
    setText(clip.cover_text);
    setWord(clip.cover_word);
    setAt(clip.cover_time);
  }, [clip.id, clip.cover_text, clip.cover_word, clip.cover_time]);

  const words = text.trim().toUpperCase().split(/\s+/).filter(Boolean);
  const coloured = bare(word.toUpperCase());
  const rendering = clip.render_status === "queued" || clip.render_status === "rendering";
  const rendered = clip.render_status === "ready";
  const textChanged = text.trim().toUpperCase() !== shown.cover_text;
  const wordChanged = coloured !== shown.cover_word;
  const changed = textChanged || wordChanged || at !== shown.cover_time;
  const length = clip.end - clip.start;
  const offset = Math.min(length, Math.max(0, (at ?? clip.start + length / 2) - clip.start));

  const send = (body: CoverBody, done: string, which: "make" | "best") => {
    setPending(which);
    return remake.mutateAsync({ id: clip.id, body })
      .then((updated) => {
        setShown(updated);
        setText(updated.cover_text);
        setWord(updated.cover_word);
        setAt(updated.cover_time);
        toast({ tone: "good", title: done });
      })
      .catch((error: Error) => toast({ tone: "bad", title: "Could not make the thumbnail", body: error.message }))
      .finally(() => setPending(null));
  };

  const make = () => send({
    ...(textChanged ? { text } : {}),
    ...(wordChanged || textChanged ? { word: coloured } : {}),
    ...(at !== null ? { time: at } : {}),
  }, rendered ? "Thumbnail updated" : "Kept for the render", "make");
  const best = () => send({ ...(textChanged ? { text } : {}), ...(wordChanged ? { word: coloured } : {}), auto: true },
    "Forge picked the best moment", "best");
  const useFrame = () => {
    const time = frameTime?.();
    if (time !== null && time !== undefined) setAt(Math.round(Math.min(clip.end, Math.max(clip.start, time)) * 100) / 100);
  };

  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-faint">Thumbnail</p>
      <div className="flex gap-4">
        <div className="relative w-[136px] shrink-0">
          {shown.cover_url ? (
            <a href={shown.cover_url} target="_blank" rel="noreferrer" title="Open it full size">
              <img src={shown.cover_url} alt={`Thumbnail: ${shown.cover_text}`} className="aspect-[9/16] w-full rounded-lg border border-line bg-black object-cover" />
            </a>
          ) : (
            <div className="flex aspect-[9/16] w-full flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-line p-3 text-center text-faint">
              <ImageIcon className="h-6 w-6" />
              <span className="text-[11px] leading-snug">{rendering ? "Made at the end of the render" : rendered ? "Not made yet: press Make thumbnail" : "Made when the clip is rendered"}</span>
            </div>
          )}
          {pending !== null && (
            <span className="absolute inset-0 flex items-center justify-center rounded-lg bg-black/55"><Spinner /></span>
          )}
        </div>

        <div className="min-w-0 flex-1 space-y-3">
          <Field label="Words on it" hint="Two to six words that make people curious. Click a word to colour it.">
            <Input value={text} maxLength={40} onChange={(event) => setText(event.target.value)} placeholder="Back to the words from the title" />
          </Field>
          {words.length > 0 && (
            <div className="flex flex-wrap gap-1" role="group" aria-label="Word in colour">
              {words.map((item, index) => (
                <button key={`${item}-${index}`} type="button" onClick={() => setWord(bare(item))} aria-pressed={bare(item) === coloured}
                  className={cx("rounded-md border px-1.5 py-0.5 text-[11px] font-semibold tracking-wide transition-colors",
                    bare(item) === coloured ? "border-[#ffd400] bg-[#ffd400] text-black" : "border-line text-ink hover:bg-hover")}>
                  {item}
                </button>
              ))}
            </div>
          )}
          <div>
            <div className="mb-1 flex items-baseline justify-between gap-2 text-[12.5px]">
              <span className="text-muted">Moment</span>
              <span className="tabular text-ink">{at === null ? "Forge picks the best one" : `${clock(offset, true)} into the clip`}</span>
            </div>
            <input type="range" min={0} max={Math.max(0.1, length)} step={0.1} value={offset} aria-label="Moment of the thumbnail"
              onChange={(event) => setAt(Math.round((clip.start + Number(event.target.value)) * 100) / 100)}
              className="w-full accent-[var(--color-accent)]" />
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" variant="primary" onClick={make} busy={pending === "make"} disabled={rendering || pending !== null || (!changed && Boolean(shown.cover_url))}>
              <RefreshCw className="h-4 w-4" />{rendered ? "Make thumbnail" : "Keep for the render"}
            </Button>
            {rendered && (
              <Button size="sm" onClick={best} busy={pending === "best"} disabled={rendering || pending !== null} title="Forge looks at the whole clip for a sharp, well-lit frame with a clear face">
                <Sparkles className="h-4 w-4" />Best moment
              </Button>
            )}
            {frameTime && (
              <Button size="sm" variant="ghost" onClick={useFrame} disabled={rendering} title="Pause the video where you like the picture, then press this">
                <Crosshair className="h-4 w-4" />Use the frame on screen
              </Button>
            )}
            {shown.cover_url && (
              <a href={`/api/clips/${clip.id}/cover?download=1`}><Button size="sm"><Download className="h-4 w-4" />Download</Button></a>
            )}
          </div>
          {rendering && <p className="text-xs text-faint">The clip is rendering; change its thumbnail when the render is done.</p>}
        </div>
      </div>
    </div>
  );
}

type Choice = "with" | "without";

/** The caption to paste into TikTok: Claude rewrites it on request, and you can change it and save it. */
export function TikTokCaption({ clip }: { clip: Clip }) {
  const toast = useToast();
  const patch = usePatchClip();
  const [text, setText] = useState(clip.post_caption);
  const [emojis, setEmojis] = useState<Choice>("with");
  const [tags, setTags] = useState<Choice>("with");
  const [writing, setWriting] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => setText(clip.post_caption), [clip.id, clip.post_caption]);
  const changed = text !== clip.post_caption;

  const rewrite = async () => {
    setWriting(true);
    try {
      const answer = await api.post<{ caption: string }>(`/clips/${clip.id}/tiktok-caption`,
        { emojis: emojis === "with", hashtags: tags === "with" });
      setText(answer.caption);
    } catch (error) {
      toast({ tone: "bad", title: "Claude could not write a caption", body: (error as Error).message });
    } finally {
      setWriting(false);
    }
  };
  const save = (value: string, title: string) =>
    patch.mutateAsync({ id: clip.id, body: { tiktok_caption: value } })
      .then(() => toast({ tone: "good", title }))
      .catch((error: Error) => toast({ tone: "bad", title: "Could not save the caption", body: error.message }));
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast({ tone: "bad", title: "Could not copy", body: "Select the text and copy it yourself." });
    }
  };

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wider text-faint">TikTok caption</p>
        <Button size="sm" variant="ghost" onClick={copy} disabled={!text.trim()}>
          {copied ? <Check className="h-4 w-4 text-good" /> : <Copy className="h-4 w-4" />}{copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Segmented value={emojis} onChange={setEmojis}
          options={[{ value: "with", label: "With emojis" }, { value: "without", label: "No emojis" }]} />
        <Segmented value={tags} onChange={setTags}
          options={[{ value: "with", label: "With hashtags" }, { value: "without", label: "No hashtags" }]} />
        <Button size="sm" onClick={rewrite} busy={writing} title="Claude writes a new caption from what is said in the clip">
          {!writing && <Sparkles className="h-4 w-4" />}{writing ? "Claude is writing…" : "Rewrite with Claude"}
        </Button>
      </div>
      <Textarea rows={5} value={text} maxLength={4000} onChange={(event) => setText(event.target.value)}
        aria-label="TikTok caption" className="text-[13px]" />
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        {changed ? (
          <>
            <Button size="sm" variant="primary" onClick={() => save(text, "Caption saved")} busy={patch.isPending}><Save className="h-4 w-4" />Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setText(clip.post_caption)}><RotateCcw className="h-4 w-4" />Undo changes</Button>
            <span className="text-xs text-faint">Not saved yet. Copy works on what is in the box.</span>
          </>
        ) : clip.tiktok_caption ? (
          <>
            <span className="text-xs text-faint">Your own caption, saved with the clip.</span>
            <Button size="sm" variant="ghost" onClick={() => save("", "Back to the automatic caption")}>Back to automatic</Button>
          </>
        ) : (
          <span className="text-xs text-faint">The clip's caption, the credit and the hashtags. Rewrite it, or change it yourself and save.</span>
        )}
      </div>
    </div>
  );
}
