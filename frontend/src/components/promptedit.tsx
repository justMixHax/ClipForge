// Prompt edit: describe in your own words what you want changed in a clip; Claude makes the change and the clip
// is rendered again. What ClipForge cannot do is listed, never faked, and the last prompt edit can be undone.
import { AlertTriangle, CheckCircle2, Undo2, WandSparkles } from "lucide-react";
import { useEffect, useState } from "react";
import type { Clip, PromptEditResult } from "../lib/api";
import { cx } from "../lib/format";
import { useClip, usePromptEdit, useUndoPromptEdit } from "../lib/hooks";
import { Button, Modal, Spinner, Textarea, useToast } from "./ui";

const EXAMPLES = [
  "Start 3 seconds earlier",
  "Cut out the part where nothing happens",
  "Bigger subtitles, one word at a time",
  "Yellow subtitles",
  "No hook title",
  "Keep it under 60 seconds",
];
const MAX = 1000;

function Lines({ done, notDone }: { done: string[]; notDone: string[] }) {
  return (
    <ul className="space-y-1">
      {done.map((line) => (
        <li key={`done-${line}`} className="flex items-start gap-2 text-[13px] text-ink">
          <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" />{line}
        </li>
      ))}
      {notDone.map((line) => (
        <li key={`not-${line}`} className="flex items-start gap-2 text-[13px] text-muted">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />{line}
        </li>
      ))}
    </ul>
  );
}

/** The prompt edit itself: what you want, Claude's changes, and undo. */
export function PromptEditPanel({ clip: given, framed = true }: { clip: Clip; framed?: boolean }) {
  const live = useClip(given.id);  // the latest copy, so the render status and the last edit stay current
  const clip = live.data && live.data.id === given.id ? live.data : given;
  const toast = useToast();
  const edit = usePromptEdit();
  const undo = useUndoPromptEdit();
  const [text, setText] = useState("");
  const [result, setResult] = useState<PromptEditResult | null>(null);
  useEffect(() => { setText(""); setResult(null); }, [clip.id]);
  const rendering = clip.render_status === "queued" || clip.render_status === "rendering";
  const busy = edit.isPending;
  const last = clip.last_edit;

  const submit = async () => {
    const instruction = text.trim();
    if (!instruction) return;
    setResult(null);
    try {
      const answer = await edit.mutateAsync({ id: clip.id, instruction });
      setResult(answer);
      if (answer.rendering) {
        setText("");
        toast({ tone: "neutral", title: "Rendering again with your change", body: answer.summary || clip.title });
      } else {
        toast({ tone: "warn", title: "Nothing was changed", body: answer.not_done[0] ?? "Claude found nothing to change." });
      }
    } catch (error) {
      toast({ tone: "bad", title: "The prompt edit did not work", body: (error as Error).message });
    }
  };
  const takeBack = async () => {
    try {
      await undo.mutateAsync(clip.id);
      setResult(null);
      toast({ tone: "neutral", title: "Back as it was", body: "Rendering the clip again without the prompt edit." });
    } catch (error) {
      toast({ tone: "bad", title: "Could not undo", body: (error as Error).message });
    }
  };
  const add = (example: string) => setText((current) => {
    const base = current.trim();
    return (base ? `${base.replace(/[.,;]$/, "")}, ${example.charAt(0).toLowerCase()}${example.slice(1)}` : example).slice(0, MAX);
  });

  // the latest answer when it changed nothing; otherwise the edit saved with the clip
  const shown = result && !result.rendering ? result : last ? { summary: last.summary, done: last.done, not_done: last.not_done } : null;
  return (
    <section className={cx(framed && "rounded-xl border border-line bg-bg/60 p-3.5")}>
      {framed && (
        <div className="mb-2 flex items-center gap-2">
          <WandSparkles className="h-4 w-4 text-accent-soft" />
          <p className="text-xs font-medium uppercase tracking-wider text-faint">Prompt edit</p>
        </div>
      )}
      <p className="text-[13px] text-muted">Describe what you want changed: the cut, parts to leave out, the subtitles, the hook title, the layout or the sound. Claude makes the change and the clip is rendered again.</p>
      <Textarea rows={3} className="mt-2.5" value={text} maxLength={MAX} disabled={busy}
        placeholder={"e.g. Start when he says “okay ladies”, cut out the part where he reads chat, and make the subtitles bigger and yellow"}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !rendering) submit(); }} />
      <div className="mt-2 flex flex-wrap gap-1.5">
        {EXAMPLES.map((example) => (
          <button key={example} type="button" disabled={busy} onClick={() => add(example)}
            className="rounded-full border border-line px-2.5 py-1 text-xs text-muted transition-colors hover:border-line-strong hover:text-ink disabled:opacity-50">
            {example}
          </button>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" onClick={submit} busy={busy} disabled={!text.trim() || rendering}
          title={rendering ? "Wait until the clip has finished rendering" : "Claude changes the clip as you describe, then it is rendered again (Ctrl+Enter)"}>
          {!busy && <WandSparkles className="h-4 w-4" />}Change and render again
        </Button>
        <span className="text-xs text-faint tabular">{text.length}/{MAX}</span>
        {rendering && !busy && <span className="text-xs text-muted">The clip is rendering; you can describe the next change when it is done.</span>}
      </div>
      {busy && (
        <p className="mt-3 flex items-center gap-2 text-[13px] text-muted"><Spinner />Claude is reading your request and the clip. This can take up to a minute.</p>
      )}
      {shown && !busy && (
        <div className="mt-3 space-y-2 rounded-lg border border-line bg-panel/60 p-3">
          {last && shown !== result && <p className="text-xs text-faint">Last prompt edit: “{last.instruction}”</p>}
          {shown.summary && <p className="text-[13px] text-ink">{shown.summary}</p>}
          <Lines done={shown.done} notDone={shown.not_done} />
          {last && (!result || result.rendering) && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Button size="sm" variant="ghost" onClick={takeBack} busy={undo.isPending} disabled={rendering}
                title={rendering ? "Undo is possible once this render is done" : "Put the clip back as it was before this prompt edit and render it again"}>
                {!undo.isPending && <Undo2 className="h-4 w-4" />}Undo this prompt edit
              </Button>
              {rendering && <span className="text-xs text-muted">Rendering… the new video shows here when it is done.</span>}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/** The Prompt edit button on a clip card: opens the prompt edit for that clip. */
export function PromptEditButton({ clip }: { clip: Clip }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)} title="Describe what you want changed; Claude edits the clip and it is rendered again">
        <WandSparkles className="h-4 w-4" />Prompt edit
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title={`Prompt edit · ${clip.title}`}>
        <PromptEditPanel clip={clip} framed={false} />
      </Modal>
    </>
  );
}
