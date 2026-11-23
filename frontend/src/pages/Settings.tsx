import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Cpu, Download, HardDrive, KeyRound, RefreshCw, SlidersHorizontal, Sparkles, XCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Badge, Button, Card, CardHeader, ErrorNote, Field, Input, Segmented, Select, Spinner, Switch, useToast } from "../components/ui";
import { api, type HardwareReport, type Options, type SettingsBody, type StorageUsage } from "../lib/api";
import { bytes } from "../lib/format";
import { useFeatures, useHardware, useOptions, useSettings } from "../lib/hooks";
import { useQuery } from "@tanstack/react-query";
import { FeatureRow } from "./Planned";

function Tested({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <p className="flex items-start gap-2 text-[13px]">
      {ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-good" /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-faint" />}
      <span className="min-w-0 text-muted">{children}</span>
    </p>
  );
}

function HardwareCard() {
  const hardware = useHardware();
  const client = useQueryClient();
  const [testing, setTesting] = useState(false);
  const retest = async () => {
    setTesting(true);
    try {
      client.setQueryData(["hardware"], await api.post<HardwareReport>("/system/hardware/refresh"));
      client.invalidateQueries({ queryKey: ["features"] });
    } finally {
      setTesting(false);
    }
  };
  const hw = hardware.data?.hardware;
  const rec = hardware.data?.recommended;
  return (
    <Card>
      <CardHeader title="This PC" icon={<Cpu className="h-4 w-4 text-faint" />} hint="Detected and tested. The graphics card is only used for what passed its test."
        action={<Button size="sm" onClick={retest} busy={testing}><RefreshCw className="h-3.5 w-3.5" />Test again</Button>} />
      <div className="space-y-2.5 px-5 pb-5">
        {!hw ? <Spinner /> : (
          <>
            <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
              <div className="flex justify-between gap-3"><dt className="text-faint">Processor</dt><dd className="truncate text-ink" title={hw.cpu.name}>{hw.cpu.cores} cores · {hw.cpu.threads} threads</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-faint">Memory</dt><dd className="text-ink">{hw.memory.total_gb} GB</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-faint">Graphics card</dt><dd className="truncate text-ink">{hw.gpus[0] ? `${hw.gpus[0].name} · ${Math.round((hw.gpus[0].memory_total_mb ?? 0) / 1024)} GB` : "None detected"}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-faint">FFmpeg</dt><dd className="truncate text-ink" title={hw.ffmpeg}>{hw.ffmpeg.split("-")[0] || "not found"}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-faint">Free disk space</dt><dd className="text-ink">{hw.storage.free_gb} GB</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-faint">Tested</dt><dd className="text-ink">{hw.detected_at.replace("T", " ").slice(0, 16)} UTC</dd></div>
            </dl>
            <div className="space-y-1.5 border-t border-line pt-3">
              <Tested ok={hw.encoders.libx264?.ok}>Video encoding on the processor (x264): {hw.encoders.libx264?.detail}</Tested>
              <Tested ok={hw.encoders.h264_nvenc?.ok}>Video encoding on the graphics card (NVENC): {hw.encoders.h264_nvenc?.detail}</Tested>
              <Tested ok={hw.whisper_cuda.ok}>Speech-to-text on the graphics card (CUDA): {hw.whisper_cuda.detail}</Tested>
              <Tested ok={hw.face_detector}>Face detector for reframing: {hw.face_detector ? "installed" : "model file missing; run scripts\\setup.ps1"}</Tested>
            </div>
            {rec && (
              <p className="rounded-lg bg-bg/60 px-3 py-2 text-[13px] text-muted">
                In use: <span className="text-ink">{rec.video_encoder === "h264_nvenc" ? "graphics card" : "processor"}</span> for encoding,{" "}
                <span className="text-ink">{rec.whisper_device === "cuda" ? "graphics card" : "processor"}</span> for speech-to-text with model{" "}
                <span className="text-ink">{rec.whisper_model ?? "none installed"}</span>.
              </p>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

function ModelsCard({ options }: { options: Options }) {
  const client = useQueryClient();
  const toast = useToast();
  const installing = options.speech_models.some((m) => m.install?.state === "running");
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => client.invalidateQueries({ queryKey: ["options"] }), 2000);
    return () => window.clearInterval(timer);
  }, [installing, client]);
  const install = async (key: string, sizeMb: number, local: boolean) => {
    if (!local && !window.confirm(`Download this model from Hugging Face? About ${sizeMb} MB.`)) return;
    try {
      await api.post(`/models/${key}/install`);
      client.invalidateQueries({ queryKey: ["options"] });
    } catch (e) {
      toast({ tone: "bad", title: "Could not start the install", body: (e as Error).message });
    }
  };
  return (
    <Card>
      <CardHeader title="Speech models" icon={<Sparkles className="h-4 w-4 text-faint" />} hint="Run on this PC. Nothing is downloaded unless you press Install." />
      <ul className="divide-y divide-line px-5 pb-3">
        {options.speech_models.map((model) => (
          <li key={model.key} className="flex items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="text-[13.5px] text-ink">{model.label} <span className="text-faint">· about {model.size_mb >= 1000 ? `${(model.size_mb / 1000).toFixed(1)} GB` : `${model.size_mb} MB`}</span></p>
              <p className="text-xs text-muted">{model.install?.state === "running" || model.install?.state === "failed" ? model.install.message : model.note}</p>
            </div>
            {model.installed ? <Badge tone="good"><CheckCircle2 className="h-3 w-3" />Installed</Badge>
              : model.install?.state === "running" ? <Badge tone="accent"><Spinner className="h-3 w-3 text-accent" />Installing</Badge>
              : <Button size="sm" onClick={() => install(model.key, model.size_mb, model.on_disk_elsewhere)}><Download className="h-3.5 w-3.5" />{model.on_disk_elsewhere ? "Copy from this PC" : "Install"}</Button>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function SecretField({ name, label, stored, onSaved }: { name: string; label: string; stored: boolean; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async (next: string) => {
    setBusy(true);
    try {
      await api.put("/settings/secret", { name, value: next });
      setValue("");
      onSaved();
      toast({ tone: "good", title: next ? "Key stored (encrypted)" : "Key removed" });
    } catch (e) {
      toast({ tone: "bad", title: "Could not save the key", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Field label={label} hint={stored ? "A key is stored. It is encrypted on this PC and is never shown again." : "Not set."}>
      <div className="flex gap-2">
        <Input type="password" autoComplete="off" value={value} onChange={(event) => setValue(event.target.value)} placeholder={stored ? "••••••••  (stored)" : "Paste the key"} />
        <Button onClick={() => save(value)} busy={busy} disabled={!value.trim()}>Save</Button>
        {stored && <Button variant="ghost" onClick={() => save("")} disabled={busy}>Remove</Button>}
      </div>
    </Field>
  );
}

type Connection = { ok: boolean; detail: string; model: string; seconds: number };

function ConnectionTest() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ claude: Connection; chatgpt: Connection } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<{ claude: Connection; chatgpt: Connection }>("/system/ai/test"));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const row = (name: string, c: Connection) => (
    <p className="flex items-start gap-1.5 text-[12.5px]">
      {c.ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-good" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-bad" />}
      <span><span className="font-medium text-ink">{name}</span> <span className="text-muted">({c.model}, {c.seconds} s):</span> <span className={c.ok ? "text-ink" : "text-bad"}>{c.detail}</span></span>
    </p>
  );
  return (
    <div className="space-y-2 rounded-xl border border-line bg-bg/60 p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[13px] text-muted">Send a one-word test to Claude and ChatGPT to check both are connected.</p>
        <Button size="sm" onClick={run} busy={busy}>Test connections</Button>
      </div>
      <ErrorNote error={error} />
      {result && <div className="space-y-1">{row("Claude", result.claude)}{row("ChatGPT", result.chatgpt)}</div>}
    </div>
  );
}

/** The ChatGPT model, with the models your ChatGPT plan offers right now as suggestions. */
function ChatGPTModelField({ value, disabled, onSave }: { value: string; disabled: boolean; onSave: (model: string) => void }) {
  const offered = useQuery({ queryKey: ["chatgpt-models"], queryFn: () => api.get<{ offered: string[] }>("/system/ai/chatgpt-models"), staleTime: 60_000 });
  const models = offered.data?.offered ?? [];
  const missing = models.length > 0 && !models.includes(value);
  const hint = models.length === 0 ? "As named in the ChatGPT app, e.g. gpt-6.2-astra."
    : missing ? `Your ChatGPT plan does not offer ${value} right now. It offers: ${models.join(", ")}.`
      : `Your ChatGPT plan offers: ${models.join(", ")}.`;
  return (
    <Field label="ChatGPT model" hint={hint}>
      <Input defaultValue={value} key={value} list="chatgpt-models" disabled={disabled} spellCheck={false} className={missing ? "border-warn/60" : undefined}
        onBlur={(event) => event.target.value.trim() && event.target.value.trim() !== value && onSave(event.target.value.trim())} />
      <datalist id="chatgpt-models">{models.map((model) => <option key={model} value={model} />)}</datalist>
    </Field>
  );
}

export default function SettingsPage() {
  const settings = useSettings();
  const options = useOptions();
  const features = useFeatures();
  const client = useQueryClient();
  const toast = useToast();
  const storage = useQuery({ queryKey: ["storage"], queryFn: () => api.get<StorageUsage>("/system/storage") });
  const [keywords, setKeywords] = useState("");
  const [exportFolder, setExportFolder] = useState("");

  const values = settings.data?.values;
  useEffect(() => {
    if (!values) return;
    setKeywords((values["niche.keywords"] as string[]).join(", "));
    setExportFolder(String(values["export.folder"] ?? ""));
  }, [values]);

  const save = async (patch: Record<string, unknown>) => {
    try {
      client.setQueryData(["settings"], await api.put<SettingsBody>("/settings", { values: patch }));
      client.invalidateQueries({ queryKey: ["hardware"] });
      client.invalidateQueries({ queryKey: ["features"] });
    } catch (e) {
      toast({ tone: "bad", title: "Setting not saved", body: (e as Error).message });
    }
  };
  const cleanup = async (what: string, question: string) => {
    if (!window.confirm(question)) return;
    try {
      const result = await api.post<{ freed_bytes: number }>("/system/cleanup", { what });
      toast({ tone: "good", title: "Cleaned up", body: `${bytes(result.freed_bytes)} freed.` });
      storage.refetch();
    } catch (e) {
      toast({ tone: "bad", title: "Cleanup failed", body: (e as Error).message });
    }
  };

  if (settings.isLoading || options.isLoading) return <div className="flex justify-center py-24"><Spinner className="h-6 w-6" /></div>;
  if (!values || !options.data) return <ErrorNote error={settings.error ?? options.error ?? new Error("Could not load settings")} />;
  const v = (key: string) => values[key];
  const installed = options.data.speech_models.filter((m) => m.installed);

  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <div className="space-y-6">
        <HardwareCard />
        <ModelsCard options={options.data} />

        <Card>
          <CardHeader title="Processing" icon={<SlidersHorizontal className="h-4 w-4 text-faint" />} />
          <div className="space-y-4 px-5 pb-5">
            <Field label="Priority" group hint="Speed uses quicker encodes; quality takes longer for smaller, cleaner files.">
              <Segmented value={String(v("processing.priority"))} onChange={(value) => save({ "processing.priority": value })}
                options={[{ value: "speed", label: "Speed" }, { value: "balanced", label: "Balanced" }, { value: "quality", label: "Quality" }]} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Speech model">
                <Select value={String(v("processing.whisper_model"))} onChange={(event) => save({ "processing.whisper_model": event.target.value })}>
                  <option value="auto">Automatic (best installed)</option>
                  {installed.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                </Select>
              </Field>
              <Field label="Speech-to-text runs on" hint="The graphics card is used only if its test passed.">
                <Select value={String(v("processing.whisper_device"))} onChange={(event) => save({ "processing.whisper_device": event.target.value })}>
                  <option value="auto">Automatic</option><option value="cpu">Processor</option><option value="cuda">Graphics card</option>
                </Select>
              </Field>
              <Field label="Who picks the clips" hint="Claude uses your Claude plan through the Claude Code tool on this PC. If it is signed out or the limit is reached, the local rules pick and the job says why.">
                <Select value={String(v("ai.engine"))} onChange={(event) => save({ "ai.engine": event.target.value })}>
                  <option value="claude">Claude (recommended)</option><option value="local">Local rules only</option>
                </Select>
              </Field>
              <Field label="Claude model" hint="Sonnet is the best balance. Opus is stronger but uses your plan faster; Haiku is lightest.">
                <Select value={String(v("ai.claude_model"))} onChange={(event) => save({ "ai.claude_model": event.target.value })} disabled={v("ai.engine") !== "claude"}>
                  <option value="sonnet">Sonnet</option><option value="opus">Opus</option><option value="haiku">Haiku</option>
                </Select>
              </Field>
              <Field label="Second opinion" hint="ChatGPT checks the picks through the Codex tool from the ChatGPT app and your ChatGPT plan, and may add up to two missed moments. Nothing is removed: you decide.">
                <Select value={v("ai.engine") === "claude" ? String(v("ai.second_opinion")) : "off"} disabled={v("ai.engine") !== "claude"} onChange={(event) => save({ "ai.second_opinion": event.target.value })}>
                  <option value="chatgpt">ChatGPT (recommended)</option><option value="off">Off</option>
                </Select>
              </Field>
              <ChatGPTModelField value={String(v("ai.chatgpt_model"))} disabled={v("ai.second_opinion") !== "chatgpt"}
                onSave={(model) => save({ "ai.chatgpt_model": model })} />
              <div className="sm:col-span-2"><ConnectionTest /></div>
              <Field label="Video encoder">
                <Select value={String(v("processing.video_encoder"))} onChange={(event) => save({ "processing.video_encoder": event.target.value })}>
                  <option value="auto">Automatic</option><option value="libx264">Processor (x264)</option><option value="h264_nvenc">Graphics card (NVENC)</option>
                </Select>
              </Field>
            </div>
            <Switch checked={Boolean(v("audio.normalize"))} onChange={(value) => save({ "audio.normalize": value })} label="Level out the volume" hint="Brings every clip to the loudness platforms expect (-14 LUFS)." />
            <Switch checked={Boolean(v("captions.censor"))} onChange={(value) => save({ "captions.censor": value })} label="Mask swear words in burned-in captions" hint="On screen only (f***). Transcripts, SRT and VTT files stay word for word." />
          </div>
        </Card>

        <Card>
          <CardHeader title="Your niche" hint="Words that matter to your audience. Clips mentioning them rank a little higher." />
          <div className="space-y-4 px-5 pb-5">
            <Field label="Keywords, separated by commas">
              <Input value={keywords} onChange={(event) => setKeywords(event.target.value)} placeholder="gta, heist, speedrun"
                onBlur={() => save({ "niche.keywords": keywords.split(",").map((k) => k.trim().toLowerCase()).filter(Boolean).slice(0, 50) })} />
            </Field>
            <Field label="Export folder" hint="Where “Export to folder” in the library copies clips. Empty: the app's own exports folder.">
              <Input value={exportFolder} onChange={(event) => setExportFolder(event.target.value)} placeholder="D:\Clips" spellCheck={false} onBlur={() => save({ "export.folder": exportFolder.trim() })} />
            </Field>
          </div>
        </Card>
      </div>

      <div className="space-y-6">
        <Card>
          <CardHeader title="Storage" icon={<HardDrive className="h-4 w-4 text-faint" />} hint={storage.data ? `${bytes(storage.data.total)} used · ${bytes(storage.data.disk_free)} free · ${storage.data.data_dir}` : undefined} />
          <div className="space-y-3 px-5 pb-5">
            {storage.data && (
              <ul className="grid grid-cols-2 gap-x-6 gap-y-1 text-[13px]">
                {[["uploads", "Source videos"], ["clips", "Rendered clips"], ["work", "Working files"], ["models", "AI models"], ["exports", "Exports"], ["logs", "Logs"]].map(([key, label]) => (
                  <li key={key} className="flex justify-between"><span className="text-muted">{label}</span><span className="tabular text-ink">{bytes(storage.data.areas[key] ?? 0)}</span></li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap gap-2 border-t border-line pt-3">
              <Button size="sm" onClick={() => cleanup("work", "Delete working files (extracted audio and measurements) of videos that are not being processed? Transcripts and clips are kept; analysing a video again re-creates what it needs.")}>Clear working files</Button>
              <Button size="sm" onClick={() => cleanup("rejected", "Delete the rendered files of rejected clips? The clips stay in the library and can be rendered again.")}>Delete rejected renders</Button>
              <Button size="sm" onClick={() => cleanup("failed_jobs", "Remove finished jobs older than a week from the activity list?")}>Trim old job history</Button>
            </div>
            <p className="text-xs text-faint">Cleanup never touches source videos, approved clips, or anything outside the data folder.</p>
          </div>
        </Card>

        <Card>
          <CardHeader title="What your feedback changes" hint="Each mark shifts ranking by a fixed, capped amount. Nothing hidden." />
          <div className="px-5 pb-5 text-[13px]">
            {settings.data && settings.data.feedback.count === 0 ? (
              <p className="text-muted">No feedback yet. Use the Feedback menu on a clip (good, wrong moment, needs too much context…) and the effect will be listed here.</p>
            ) : (
              <ul className="space-y-1 text-muted">
                {settings.data?.feedback.effects.map((effect) => <li key={effect}>• {effect}</li>)}
                {settings.data?.feedback.effects.length === 0 && <li>{settings.data.feedback.count} marks stored; none of them change ranking yet.</li>}
              </ul>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Keys for planned options" icon={<KeyRound className="h-4 w-4 text-faint" />} hint="Not needed today: Claude already picks the clips through your Claude plan (see Processing). Keys stored here wait for planned options and are not used; no paid request is ever sent." />
          <div className="space-y-4 px-5 pb-5">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Another AI provider (planned)">
                <Select value={String(v("llm.provider"))} onChange={(event) => save({ "llm.provider": event.target.value })}>
                  <option value="none">None</option><option value="ollama">Ollama (local)</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option>
                </Select>
              </Field>
              <Field label="Model name">
                <Input defaultValue={String(v("llm.model"))} onBlur={(event) => save({ "llm.model": event.target.value.trim() })} placeholder="e.g. a model you have pulled" />
              </Field>
            </div>
            <SecretField name="llm.openai_api_key" label="OpenAI API key" stored={Boolean(settings.data?.secrets["llm.openai_api_key"])} onSaved={() => settings.refetch()} />
            <SecretField name="llm.anthropic_api_key" label="Anthropic API key" stored={Boolean(settings.data?.secrets["llm.anthropic_api_key"])} onSaved={() => settings.refetch()} />
            <SecretField name="youtube.api_key" label="YouTube Data API key (for the planned Creator Discovery)" stored={Boolean(settings.data?.secrets["youtube.api_key"])} onSaved={() => settings.refetch()} />
          </div>
        </Card>

        <Card>
          <CardHeader title="What works, and what does not yet" hint="The honest status of every part of ClipForge on this PC." />
          <div className="px-5 pb-3">
            {features.isLoading ? <Spinner /> : (
              <ul className="divide-y divide-line">{features.data?.map((feature) => <FeatureRow key={feature.key} feature={feature} />)}</ul>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
