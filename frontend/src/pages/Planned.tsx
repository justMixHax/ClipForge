// Pages for the parts of the product that are not built yet. They say so plainly, list what each part
// needs, and point at what already works today. No mock data, no buttons that pretend.
import { BarChart3, BrainCircuit, CheckCircle2, CircleDashed, CircleSlash, Lock, Workflow, Wrench } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, CardHeader, Spinner, type Tone } from "../components/ui";
import type { Feature } from "../lib/api";
import { useFeatures } from "../lib/hooks";

type Area = "analytics" | "automation" | "strategist";

const PAGES: Record<Area, { title: string; icon: ReactNode; summary: string; features: string[]; needs: string[]; today: { text: string; to: string; label: string } }> = {
  analytics: {
    title: "Analytics", icon: <BarChart3 className="h-6 w-6" />,
    summary: "Track how posted clips perform and compare the scores ClipForge gave them with what actually happened.",
    features: ["analytics", "feedback"],
    needs: [
      "Connected accounts (see Publishing Studio), or numbers you type in yourself, clearly marked as such.",
      "Enough posted clips to compare predictions with results honestly.",
    ],
    today: { text: "Your feedback on suggestions already adjusts future ranking. The current effect is shown in Settings.", to: "/settings", label: "Open Settings" },
  },
  automation: {
    title: "Automation Center", icon: <Workflow className="h-6 w-6" />,
    summary: "Rules such as: when a followed creator uploads, analyse it, render the top five, and tell me.",
    features: ["automation", "discovery", "resume", "notifications"],
    needs: [
      "Rules that act on what Creator Discovery finds. Today it shows new uploads and you choose which to import.",
      "Publishing, for any step that posts. Automatic posting will be opt-in, reviewable and stoppable.",
    ],
    today: { text: "Creator Discovery already looks at the channels you follow by itself, and the queue processes several videos one after another and survives restarts.", to: "/discovery", label: "Open Creator Discovery" },
  },
  strategist: {
    title: "AI Strategist", icon: <BrainCircuit className="h-6 w-6" />,
    summary: "Ask what to clip next, why a clip did well, or which style works for your accounts, answered from your own recorded results.",
    features: ["strategist", "llm_pass", "analytics"],
    needs: [
      "Analytics, so answers rest on real results rather than guesses.",
      "A language model: a local one through Ollama, or an API key you choose to add. Paid requests will show a cost estimate first.",
    ],
    today: { text: "Every suggestion already comes with its score breakdown and the evidence behind it.", to: "/library", label: "Open the Clip Library" },
  },
};

const STATUS: Record<Feature["status"], { label: string; tone: Tone; icon: ReactNode }> = {
  working: { label: "Working", tone: "good", icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
  needs_setup: { label: "Needs setup", tone: "warn", icon: <Wrench className="h-3.5 w-3.5" /> },
  unavailable: { label: "Not available on this PC", tone: "neutral", icon: <CircleSlash className="h-3.5 w-3.5" /> },
  blocked: { label: "Blocked by a third party", tone: "bad", icon: <Lock className="h-3.5 w-3.5" /> },
  planned: { label: "Not built yet", tone: "neutral", icon: <CircleDashed className="h-3.5 w-3.5" /> },
};

export function FeatureRow({ feature }: { feature: Feature }) {
  const status = STATUS[feature.status];
  return (
    <li className="flex items-start gap-3 py-2.5">
      <Badge tone={status.tone} className="mt-0.5 shrink-0">{status.icon}{status.label}</Badge>
      <div className="min-w-0">
        <p className="text-[13.5px] text-ink">{feature.name}</p>
        <p className="text-[12.5px] text-muted">{feature.detail}</p>
      </div>
    </li>
  );
}

export default function Planned({ area }: { area: Area }) {
  const page = PAGES[area];
  const features = useFeatures();
  const relevant = page.features.map((key) => features.data?.find((f) => f.key === key)).filter(Boolean) as Feature[];
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="rounded-3xl border border-line bg-panel px-7 py-8">
        <div className="flex items-center gap-3 text-accent">{page.icon}<Badge>Not built yet</Badge></div>
        <h2 className="mt-3 font-display text-2xl font-semibold tracking-tight">{page.title}</h2>
        <p className="mt-2 text-[14px] text-muted">{page.summary}</p>
        <p className="mt-3 text-[13px] text-faint">This page has no controls because nothing here works yet. It will appear when it does.</p>
      </div>

      <Card>
        <CardHeader title="Where each part stands" />
        <div className="px-5 pb-3">
          {features.isLoading ? <Spinner /> : <ul className="divide-y divide-line">{relevant.map((feature) => <FeatureRow key={feature.key} feature={feature} />)}</ul>}
        </div>
      </Card>

      <Card>
        <CardHeader title="What it will need from you" />
        <ul className="list-disc space-y-1.5 px-9 pb-5 text-[13.5px] text-muted">{page.needs.map((need) => <li key={need}>{need}</li>)}</ul>
      </Card>

      <Card>
        <CardHeader title="What you can do today" />
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 pb-5">
          <p className="min-w-0 flex-1 text-[13.5px] text-muted">{page.today.text}</p>
          <Link to={page.today.to}><Button>{page.today.label}</Button></Link>
        </div>
      </Card>
    </div>
  );
}
