// What you are looking for: the kind of moment ClipForge should find. One press chooses it. Claude is then held to
// that kind, and its second look drops what does not hold up as one.
import { cx } from "../lib/format";
import { useOptions, useSettings } from "../lib/hooks";
import { Field, Switch } from "./ui";

const SENDS = "Sends 8 small frames of each pick to Claude, on your own plan.";

export function FocusPicker({ focus, look, onFocus, onLook }: {
  focus: string;                       // the kind chosen ("best" for any kind)
  look: boolean;                       // frames of each pick go to Claude's second look
  onFocus: (key: string) => void;
  onLook: (on: boolean) => void;
}) {
  const kinds = useOptions().data?.focuses ?? [];
  const values = useSettings().data?.values;
  const claude = values ? values["ai.engine"] === "claude" : true;
  const secondLook = values ? Boolean(values["ai.check"]) : true;
  const chosen = kinds.find((kind) => kind.key === focus);
  return (
    <div className="space-y-2.5">
      <Field label="What are you looking for?" group hint={chosen?.hint}>
        <div className="flex flex-wrap gap-1.5">
          {kinds.map((kind) => (
            <button key={kind.key} type="button" aria-pressed={kind.key === focus} onClick={() => onFocus(kind.key)}
              className={cx("rounded-full border px-3 py-1 text-[12.5px] transition-colors",
                kind.key === focus ? "border-accent/60 bg-accent/10 text-accent-soft" : "border-line text-muted hover:border-line-strong hover:text-ink")}>
              {kind.label}
            </button>
          ))}
        </div>
      </Field>
      {claude ? (
        <Switch checked={look && secondLook} onChange={onLook} disabled={!secondLook} label="Let Claude look at the picture too"
          hint={!secondLook ? "Needs Claude's second look, which is switched off in Settings."
            : chosen?.led_by === "picture" && !look ? `This kind is carried by the picture, so Claude judges it much better when it can look. ${SENDS}`
              : `${SENDS} Off: Claude reads the transcript only.`} />
      ) : (
        <p className="text-xs text-faint">The local rules can only approximate a kind of moment. With Claude as the engine (Settings), every pick is held to it.</p>
      )}
    </div>
  );
}
