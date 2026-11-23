import {
  BarChart3, Bell, BrainCircuit, Compass, LayoutDashboard, Library, Menu, Power, Search, Send, Settings,
  SlidersHorizontal, Sparkles, Workflow, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, query, type SearchHit } from "../lib/api";
import { ago, clock, cx } from "../lib/format";
import { useActiveJobs, useNotifications, useRefreshAll } from "../lib/hooks";
import { Badge, Button, Input, Modal, Progress, Spinner, useToast } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, built: true },
  { to: "/clipper", label: "AI Clipper", icon: Sparkles, built: true },
  { to: "/discovery", label: "Creator Discovery", icon: Compass, built: false },
  { to: "/library", label: "Clip Library", icon: Library, built: true },
  { to: "/editor", label: "Video Editor", icon: SlidersHorizontal, built: true },
  { to: "/publishing", label: "Publishing Studio", icon: Send, built: true },
  { to: "/analytics", label: "Analytics", icon: BarChart3, built: false },
  { to: "/automation", label: "Automation Center", icon: Workflow, built: false },
  { to: "/strategist", label: "AI Strategist", icon: BrainCircuit, built: false },
  { to: "/settings", label: "Settings", icon: Settings, built: true },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-accent to-[#ff4d2e] shadow-[0_6px_18px_-6px_rgba(255,122,61,.8)]">
        <svg viewBox="0 0 24 24" className="h-4 w-4 translate-x-[1px] fill-[#1a0d05]"><path d="M7 4.5v15l12-7.5z" /></svg>
      </div>
      <div className="leading-tight">
        <p className="font-display text-[15px] font-semibold tracking-tight text-ink">ClipForge</p>
        <p className="text-[10.5px] uppercase tracking-[0.14em] text-faint">Local studio</p>
      </div>
    </div>
  );
}

function NavItem({ to, label, icon: Icon, built, onNavigate }: (typeof NAV)[number] & { onNavigate?: () => void }) {
  return (
    <NavLink to={to} end={to === "/"} onClick={onNavigate} title={built ? undefined : "Not built yet: this page says what is planned"}
      className={({ isActive }) => cx("group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition-colors",
        isActive ? "bg-raised text-ink" : built ? "text-muted hover:bg-hover hover:text-ink" : "text-faint hover:bg-hover hover:text-muted")}>
      {({ isActive }) => (
        <>
          <Icon className={cx("h-4 w-4 shrink-0", isActive ? "text-accent" : built ? "text-faint group-hover:text-muted" : "text-faint/70")} />
          <span className="flex-1 truncate">{label}</span>
        </>
      )}
    </NavLink>
  );
}

function Sidebar({ onNavigate, onShutDown }: { onNavigate?: () => void; onShutDown: () => void }) {
  const jobs = useActiveJobs().data ?? [];
  const running = jobs.find((job) => job.status === "running");
  return (
    <div className="flex h-full flex-col gap-5 px-3 py-4">
      <Logo />
      <nav className="flex-1 space-y-0.5 overflow-y-auto" aria-label="Main">
        {/* what works first; what is planned in its own group, so every name shows in full */}
        {NAV.filter((item) => item.built).map((item) => <NavItem key={item.to} {...item} onNavigate={onNavigate} />)}
        <p className="px-2.5 pb-1 pt-4 text-[10.5px] font-medium uppercase tracking-[0.12em] text-faint">Coming later</p>
        {NAV.filter((item) => !item.built).map((item) => <NavItem key={item.to} {...item} onNavigate={onNavigate} />)}
      </nav>
      <div className="rounded-xl border border-line bg-bg/60 p-3">
        {running ? (
          <>
            <p className="truncate text-xs text-muted">{running.message || "Working…"}</p>
            <Progress value={running.progress} busy className="mt-2" />
            <p className="mt-1.5 text-[11px] text-faint">{jobs.length - 1 > 0 ? `${jobs.length - 1} more waiting` : "Nothing else waiting"}</p>
          </>
        ) : jobs.length > 0 ? (
          <p className="text-xs text-muted">{jobs.length} job{jobs.length > 1 ? "s" : ""} waiting for the worker…</p>
        ) : (
          <p className="text-xs text-faint">Idle. Everything runs on this PC.</p>
        )}
        <ShutDownButton jobs={jobs.length} onDone={onShutDown} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- shutting down
function ShutDownButton({ jobs, onDone }: { jobs: number; onDone: () => void }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const stop = async () => {
    setBusy(true);
    try {
      await api.post("/system/shutdown");
      setAsking(false);
      onDone();
    } catch (error) {
      toast({ tone: "bad", title: "ClipForge could not shut down", body: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" onClick={() => setAsking(true)}
        className="mt-2.5 inline-flex items-center gap-1.5 text-xs text-faint transition-colors hover:text-bad">
        <Power className="h-3.5 w-3.5" />Shut down ClipForge
      </button>
      <Modal open={asking} onClose={() => setAsking(false)} title="Shut down ClipForge?"
        footer={(
          <>
            <Button variant="ghost" onClick={() => setAsking(false)}>Cancel</Button>
            <Button variant="danger" onClick={stop} busy={busy}>{!busy && <Power className="h-4 w-4" />}Shut down</Button>
          </>
        )}>
        <p className="text-[13px] text-muted">
          ClipForge stops on this PC until you start it again with the <span className="text-ink">ClipForge</span> shortcut
          on your desktop. Your videos, clips and settings are kept.
        </p>
        {jobs > 0 && (
          <p className="mt-2 text-[13px] text-warn">
            {jobs === 1 ? "1 job is not finished. It pauses and continues" : `${jobs} jobs are not finished. They pause and continue`} when
            ClipForge starts again.
          </p>
        )}
      </Modal>
    </>
  );
}

/** Shown once ClipForge has shut down. It opens the app again by itself once it is running again. */
function ShutDownScreen() {
  useEffect(() => {
    let sawItStop = false;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        if (!response.ok) sawItStop = true;
        else if (sawItStop) window.location.reload();
      } catch {
        sawItStop = true;
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="app-glow flex min-h-screen items-center justify-center p-6">
      <div className="max-w-md rounded-2xl border border-line bg-panel p-8 text-center animate-rise">
        <Power className="mx-auto h-8 w-8 text-faint" />
        <h1 className="mt-4 text-lg font-semibold text-ink">ClipForge is shut down</h1>
        <p className="mt-2 text-[13px] text-muted">
          Nothing of it runs on this PC now. Your videos, clips and settings are kept, and unfinished jobs continue next time.
        </p>
        <p className="mt-4 text-[13px] text-muted">
          To start it again, double-click <span className="text-ink">ClipForge</span> on your desktop. This page opens it
          again by itself once it is running.
        </p>
      </div>
    </div>
  );
}

function SearchBox() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const refresh = useRefreshAll();

  const run = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.get<{ hits: SearchHit[] }>(`/search${query({ q: text })}`);
      setHits(result.hits);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const makeClip = async (hit: SearchHit) => {
    try {
      const clip = await api.post<{ id: number }>(`/videos/${hit.video_id}/clips`, { start: hit.clip_start, end: hit.clip_end, title: hit.text.slice(0, 80) });
      refresh();
      setOpen(false);
      navigate(`/editor/${clip.id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="flex h-9 w-full max-w-md items-center gap-2 rounded-lg border border-line bg-panel px-3 text-left text-[13px] text-faint transition-colors hover:border-line-strong">
        <Search className="h-4 w-4" />Search inside your videos…
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="Search inside videos">
        <form onSubmit={(event) => { event.preventDefault(); run(); }} className="flex gap-2">
          <Input autoFocus value={text} onChange={(event) => setText(event.target.value)} placeholder="e.g. the moment he falls off the map" />
          <Button variant="primary" type="submit" busy={busy}>Search</Button>
        </form>
        <p className="mt-2 text-xs text-faint">Searches what was said (keywords from transcripts). Searching by meaning is planned.</p>
        {error && <p className="mt-3 text-[13px] text-bad">{error}</p>}
        {hits && hits.length === 0 && <p className="mt-4 text-[13px] text-muted">Nothing matched. Try the exact words someone said.</p>}
        <ul className="mt-3 space-y-2">
          {hits?.map((hit) => (
            <li key={hit.segment_id} className="rounded-xl border border-line bg-bg/60 p-3">
              <p className="text-[13px] text-ink">“{hit.text}”</p>
              <div className="mt-2 flex items-center justify-between gap-2">
                <p className="truncate text-xs text-muted">{hit.video_title} · {clock(hit.start, true)}</p>
                <Button size="sm" onClick={() => makeClip(hit)} title={`Creates a clip from ${clock(hit.clip_start)} to ${clock(hit.clip_end)} that you can trim in the editor`}>Make a clip here</Button>
              </div>
            </li>
          ))}
        </ul>
      </Modal>
    </>
  );
}

function Notifications() {
  const { data } = useNotifications();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const refresh = useRefreshAll();
  const seen = useRef<number | null>(null);

  // Desktop notification for anything new, when the browser allows it.
  useEffect(() => {
    const newest = data?.items[0];
    if (!newest) return;
    if (seen.current !== null && newest.id > seen.current && !newest.read && "Notification" in window && Notification.permission === "granted") {
      new Notification(newest.title, { body: newest.body });
    }
    seen.current = Math.max(seen.current ?? 0, newest.id);
  }, [data]);

  const toggle = async () => {
    const opening = !open;
    setOpen(opening);
    if (opening && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
    if (!opening && (data?.unread ?? 0) > 0) {
      await api.post("/notifications/read");
      refresh();
    }
  };

  return (
    <div className="relative">
      <Button variant="ghost" size="icon" onClick={toggle} aria-label={`Notifications${data?.unread ? ` (${data.unread} new)` : ""}`}>
        <Bell className="h-4 w-4" />
        {(data?.unread ?? 0) > 0 && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-accent" />}
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={toggle} />
          <div className="absolute right-0 z-40 mt-2 w-[340px] rounded-2xl border border-line-strong bg-panel p-2 shadow-2xl animate-rise">
            <p className="px-2 py-1.5 text-xs font-medium uppercase tracking-wider text-faint">Notifications</p>
            {(data?.items.length ?? 0) === 0 && <p className="px-2 pb-3 text-[13px] text-muted">Nothing yet. You will be told here when clips are ready or a job fails.</p>}
            <ul className="max-h-[60vh] space-y-0.5 overflow-y-auto">
              {data?.items.map((item) => (
                <li key={item.id}>
                  <button type="button" onClick={() => { toggle(); if (item.link) navigate(item.link); }} className="w-full rounded-lg px-2 py-2 text-left hover:bg-hover">
                    <span className="flex items-start gap-2">
                      <span className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", item.read ? "bg-transparent" : item.kind === "job_failed" ? "bg-bad" : "bg-accent")} />
                      <span className="min-w-0">
                        <span className="block text-[13px] text-ink">{item.title}</span>
                        {item.body && <span className="block truncate text-xs text-muted">{item.body}</span>}
                        <span className="block text-[11px] text-faint">{ago(item.created_at)}</span>
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const [down, setDown] = useState(false);
  // Once shut down, the whole app (and every request it keeps making) gives way to the goodbye screen.
  return down ? <ShutDownScreen /> : <Shell onShutDown={() => setDown(true)}>{children}</Shell>;
}

function Shell({ children, onShutDown }: { children: ReactNode; onShutDown: () => void }) {
  const [menu, setMenu] = useState(false);
  const location = useLocation();
  const current = NAV.find((item) => (item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to)));
  const jobs = useActiveJobs();
  // When a job starts or finishes (a render done, say), every page reloads what it shows.
  const refresh = useRefreshAll();
  const signature = (jobs.data ?? []).map((job) => `${job.id}:${job.status}`).join("|");
  const previous = useRef(signature);
  useEffect(() => {
    if (previous.current !== signature) {
      previous.current = signature;
      refresh();
    }
  }, [signature, refresh]);
  return (
    <div className="app-glow flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-[232px] shrink-0 border-r border-line bg-panel/60 backdrop-blur lg:block"><Sidebar onShutDown={onShutDown} /></aside>
      {menu && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMenu(false)} />
          <aside className="absolute inset-y-0 left-0 w-[260px] border-r border-line bg-panel animate-fade"><Sidebar onNavigate={() => setMenu(false)} onShutDown={onShutDown} /></aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-bg/80 px-4 backdrop-blur lg:px-8">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMenu((v) => !v)} aria-label="Menu">{menu ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}</Button>
          <h1 className="hidden shrink-0 text-[15px] font-semibold sm:block">{current?.label ?? "ClipForge"}</h1>
          <div className="flex flex-1 justify-center px-2"><SearchBox /></div>
          {jobs.isError ? <Badge tone="bad" title="The app's server is not answering">Offline</Badge> : jobs.isLoading ? <Spinner /> : null}
          <Notifications />
        </header>
        <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
