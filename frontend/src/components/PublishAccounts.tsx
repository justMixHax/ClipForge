// Publishing → Accounts: set up your own developer app on each platform, then connect through the platform's
// own sign-in. Secrets go to the backend, are stored encrypted and never come back to this page.
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Copy, ExternalLink, KeyRound, Plug, Unplug } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, type PlatformKey, type PublishAccount, type PublishOverview, type PublishPlatform } from "../lib/api";
import { useSettings } from "../lib/hooks";
import { PlatformMark } from "./publish";
import { Badge, Button, Card, CardHeader, ErrorNote, Field, Input, NumberField, Textarea, useToast } from "./ui";

const LINK = "text-accent-soft hover:underline";

function Out({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer" className={LINK}>{children}<ExternalLink className="ml-0.5 inline h-3 w-3" /></a>;
}

const GUIDES: Record<PlatformKey, { idLabel: string; secretLabel: string; what: string; steps: (redirect: string) => ReactNode[] }> = {
  youtube: {
    idLabel: "Client ID", secretLabel: "Client secret",
    what: "Shorts go up public, unlisted or private. Scheduled Shorts are published by YouTube itself, even if this PC is off.",
    steps: () => [
      <>Open <Out href="https://console.cloud.google.com/projectcreate">Google Cloud</Out> and create a project (free).</>,
      <>In APIs &amp; Services → Library, enable <b>YouTube Data API v3</b>.</>,
      <>In <b>Google Auth Platform</b>, set up the consent screen for <b>External</b> users and add your Google account as a test user. Then, under Audience, press <b>Publish app</b>; otherwise Google signs you out every 7 days.</>,
      <>Under Clients, create a client of type <b>Desktop app</b> and copy its ID and secret into the boxes below.</>,
      <>Press <b>Connect YouTube</b>. Google warns that it has not verified the app: it is your own app, so continue.</>,
    ],
  },
  tiktok: {
    idLabel: "Client key", secretLabel: "Client secret",
    what: "Clips arrive in your TikTok inbox as drafts; you add the caption and sound and post them in the TikTok app. TikTok's rules do not let personal tools post directly.",
    steps: (redirect) => [
      <>Open <Out href="https://developers.tiktok.com/apps">TikTok for Developers</Out> and create an app.</>,
      <>Add <b>Login Kit</b> (platform: Desktop) and the <b>Content Posting API</b>, with the scopes <b>user.info.basic</b> and <b>video.upload</b>.</>,
      <>Under Login Kit, add this redirect URI exactly: <code className="break-all rounded bg-raised px-1 text-ink">{redirect}</code></>,
      <>Until TikTok has reviewed your app, switch it to <b>Sandbox</b> and add your own TikTok account as a target user.</>,
      <>Copy the client key and secret into the boxes below, then press <b>Connect TikTok</b>.</>,
    ],
  },
  instagram: {
    idLabel: "App ID", secretLabel: "App secret",
    what: "Reels are uploaded straight from this PC and published once Instagram has processed them. Meta's sign-in lasts about 60 days; ClipForge says when to paste a new token.",
    steps: () => [
      <>Your Instagram must be a <b>professional account</b> (Creator or Business, free in the Instagram app) linked to a <b>Facebook Page</b>.</>,
      <>Open <Out href="https://developers.facebook.com/apps">Meta for Developers</Out>, create an app of type <b>Business</b> and add the <b>Instagram</b> product (API setup with Facebook login).</>,
      <>Copy the <b>App ID</b> and <b>App secret</b> (App settings → Basic) into the boxes below.</>,
      <>In the <Out href="https://developers.facebook.com/tools/explorer/">Graph API Explorer</Out>, choose your app, add the permissions <b>instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement</b> and <b>business_management</b>, press Generate Access Token and sign in.</>,
      <>Paste that token below and press <b>Connect Instagram</b> within the hour.</>,
    ],
  },
};

function daysLeft(iso: string | null): number | null {
  return iso ? Math.floor((new Date(iso).getTime() - Date.now()) / 86_400_000) : null;
}

function AccountRow({ account, onDisconnect }: { account: PublishAccount; onDisconnect: () => void }) {
  const left = daysLeft(account.reconnect_by);
  return (
    <li className="flex items-start gap-3 rounded-lg border border-line bg-bg/60 p-3">
      <PlatformMark platform={account.platform} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{account.name}{account.handle && <span className="ml-1.5 font-normal text-muted">{account.handle}</span>}</p>
        {account.extra.page && <p className="text-xs text-faint">Facebook Page: {account.extra.page}</p>}
        {account.status === "needs_reconnect"
          ? <p className="mt-1 flex items-start gap-1 text-xs text-bad"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />Reconnect: {account.detail}</p>
          : left !== null && left < 14 && <p className="mt-1 text-xs text-warn">The sign-in runs out in {Math.max(0, left)} days: connect again before then.</p>}
      </div>
      {account.status === "connected" ? <Badge tone="good"><Check className="h-3 w-3" />Connected</Badge> : <Badge tone="bad">Needs reconnecting</Badge>}
      <Button size="icon" variant="ghost" title={`Disconnect ${account.name}`} onClick={onDisconnect}><Unplug className="h-4 w-4" /></Button>
    </li>
  );
}

function PlatformCard({ platform, accounts }: { platform: PublishPlatform; accounts: PublishAccount[] }) {
  const client = useQueryClient();
  const toast = useToast();
  const guide = GUIDES[platform.platform];
  const [clientId, setClientId] = useState(platform.client_id);
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);
  const refresh = () => client.invalidateQueries({ queryKey: ["publishing"] });

  const run = async (what: string, action: () => Promise<void>) => {
    setBusy(what);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const save = () => run("save", async () => {
    await api.put(`/publishing/apps/${platform.platform}`, { client_id: clientId.trim(), client_secret: secret.trim() });
    setSecret("");
    await refresh();
    toast({ tone: "good", title: `${platform.label} app saved`, body: "The secret is stored encrypted on this PC." });
  });
  const remove = () => run("remove", async () => {
    if (!window.confirm(`Remove your ${platform.label} app's ID and secret from ClipForge? Connected accounts stop working until you add them again.`)) return;
    await api.del(`/publishing/apps/${platform.platform}`);
    setClientId("");
    await refresh();
  });
  const connect = () => run("connect", async () => {
    const { url } = await api.post<{ url: string }>(`/publishing/connect/${platform.platform}`);
    window.open(url, "_blank", "noopener");
    toast({ tone: "neutral", title: `Sign in to ${platform.label} in the new tab`, body: "Come back here when it says Connected. This page updates by itself." });
  });
  const connectInstagram = () => run("connect", async () => {
    const accountsFound = await api.post<PublishAccount[]>("/publishing/instagram/connect", { token: token.trim() });
    setToken("");
    await refresh();
    toast({ tone: "good", title: "Instagram connected", body: accountsFound.map((a) => a.handle || a.name).join(", ") });
  });
  const disconnect = (account: PublishAccount) => run("disconnect", async () => {
    if (!window.confirm(`Disconnect ${account.name}? ClipForge revokes its access where ${platform.label} allows it and deletes the stored sign-in. Posts already made stay online; waiting posts are cancelled.`)) return;
    await api.post(`/publishing/accounts/${account.id}/disconnect`);
    await refresh();
    client.invalidateQueries({ queryKey: ["posts"] });
  });
  const copy = async () => {
    await navigator.clipboard.writeText(platform.redirect_uri);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Card className="flex flex-col">
      <CardHeader title={platform.label} icon={<PlatformMark platform={platform.platform} />}
        action={accounts.length ? <Badge tone="good">{accounts.length} connected</Badge> : platform.configured ? <Badge tone="info">App set up</Badge> : <Badge>Not set up</Badge>} />
      <div className="flex flex-1 flex-col gap-4 px-5 pb-5">
        <p className="text-[13px] text-muted">{guide.what}</p>

        {accounts.length > 0 && <ul className="space-y-2">{accounts.map((a) => <AccountRow key={a.id} account={a} onDisconnect={() => disconnect(a)} />)}</ul>}

        <details className="group rounded-lg border border-line bg-bg/40 p-3" open={!platform.configured}>
          <summary className="cursor-pointer select-none text-[13px] font-medium text-ink">How to set it up (once)</summary>
          <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-[13px] leading-snug text-muted">
            {guide.steps(platform.redirect_uri).map((step, index) => <li key={index}>{step}</li>)}
          </ol>
        </details>

        {platform.platform === "tiktok" && platform.redirect_uri && (
          <div className="flex items-center gap-2 text-xs text-faint">
            <span className="shrink-0">Redirect URI:</span>
            <code className="min-w-0 flex-1 truncate rounded bg-raised px-1.5 py-0.5 text-muted">{platform.redirect_uri}</code>
            <Button size="icon" variant="ghost" title="Copy" onClick={copy}>{copied ? <Check className="h-4 w-4 text-good" /> : <Copy className="h-4 w-4" />}</Button>
          </div>
        )}

        <div className="grid gap-2">
          <Field label={guide.idLabel}><Input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder={guide.idLabel} autoComplete="off" spellCheck={false} /></Field>
          <Field label={guide.secretLabel} hint={platform.configured ? "Saved and encrypted. Type a new one only to replace it." : "Stored encrypted on this PC; never shown again."}>
            <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={platform.configured ? "••••••••••••" : guide.secretLabel} autoComplete="new-password" />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button onClick={save} busy={busy === "save"} disabled={!clientId.trim() || !secret.trim()}><KeyRound className="h-4 w-4" />Save app</Button>
            {platform.configured && <Button variant="ghost" onClick={remove} busy={busy === "remove"}>Remove</Button>}
          </div>
        </div>

        {platform.connect === "token" ? (
          <div className="grid gap-2">
            <Field label="Token from the Graph API Explorer"><Textarea rows={3} value={token} onChange={(e) => setToken(e.target.value)} placeholder="EAAG…" spellCheck={false} /></Field>
            <Button variant="primary" onClick={connectInstagram} busy={busy === "connect"} disabled={!platform.configured || !token.trim()} className="justify-center"><Plug className="h-4 w-4" />Connect Instagram</Button>
          </div>
        ) : (
          <Button variant="primary" onClick={connect} busy={busy === "connect"} disabled={!platform.configured} className="justify-center">
            <Plug className="h-4 w-4" />{accounts.length ? `Connect another ${platform.label} account` : `Connect ${platform.label}`}
          </Button>
        )}
        <ErrorNote error={error} />
      </div>
    </Card>
  );
}

function DailyLimit() {
  const settings = useSettings();
  const client = useQueryClient();
  const toast = useToast();
  const value = Number(settings.data?.values["publish.daily_limit"] ?? 6);
  const save = async (next: number) => {
    try {
      await api.put("/settings", { values: { "publish.daily_limit": Math.round(next) } });
      client.invalidateQueries({ queryKey: ["settings"] });
      client.invalidateQueries({ queryKey: ["publishing"] });
    } catch (e) {
      toast({ tone: "bad", title: "Could not save", body: (e as Error).message });
    }
  };
  return (
    <Card>
      <CardHeader title="Safeguards" hint="These apply to every account." />
      <ul className="space-y-2 px-5 pb-5 text-[13px] text-muted">
        <li className="flex flex-wrap items-center gap-2">At most <NumberField value={value} min={1} max={25} onCommit={save} className="w-16" /> posts per account in any 24 hours.</li>
        <li>Only clips you approved and rendered, from a source whose right to post you recorded (Source and rights).</li>
        <li>The same clip goes to the same account once, unless you tick “Post again anyway”.</li>
        <li>A post counts as published only when the platform confirms it. A passing problem is tried again twice, then reported.</li>
      </ul>
    </Card>
  );
}

export function PublishAccounts({ overview }: { overview: PublishOverview }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-3">
        {overview.platforms.map((p) => <PlatformCard key={p.platform} platform={p} accounts={overview.accounts.filter((a) => a.platform === p.platform)} />)}
      </div>
      <DailyLimit />
    </div>
  );
}
