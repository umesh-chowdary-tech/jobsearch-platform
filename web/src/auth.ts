// Signing in. With a Supabase project: an emailed link or Google, and the
// session (and its refresh) handled by supabase-js. In dev mode on one's own
// machine: "sign in as" any email, the token kept in this browser.

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/** Who runs this copy of the service - its settings, never the repository's. */
export interface Operator { name: string; city: string; email: string }

export interface Config {
  auth: "supabase" | "dev";
  supabase_url: string;
  supabase_anon_key: string;
  name?: string;
  operator?: Operator;
}

const DEV_KEY = "jobsearch-dev-token";
let config: Config | null = null;
let supabase: SupabaseClient | null = null;

function store(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export async function loadConfig(): Promise<Config> {
  if (config) return config;
  const r = await fetch("/api/config");
  if (!r.ok) throw new Error("The service isn't answering. Try again in a moment.");
  config = (await r.json()) as Config;
  document.title = appName();
  if (config.auth === "supabase") {
    // What the emailed link brought back, read before supabase-js uses it.
    const query = new URLSearchParams(window.location.search);
    const hash = new URLSearchParams(window.location.hash.replace(/^#\/?/, ""));
    const failed = query.get("error_description") || hash.get("error_description");
    const code = query.has("code");
    // PKCE: the emailed link comes back with ?code=..., which leaves the
    // #/route part of the address to the app.
    supabase = createClient(config.supabase_url, config.supabase_anon_key, {
      auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    if (failed) {
      linkNote = `That sign-in link didn't work: ${failed}. Ask for a new one below.`;
    } else if (code && !(await supabase.auth.getSession()).data.session) {
      // The code is exchanged with a secret this browser kept when the link
      // was asked for - opened anywhere else, it can't sign anyone in.
      linkNote = "That sign-in link only works in the browser where you asked for it. Ask for a new link here, "
        + "then open the email in this same browser.";
    }
    if (failed || code) window.history.replaceState(null, "", window.location.pathname + "#/");
    rememberConsent();
  }
  return config;
}

let linkNote = "";

/** Why the sign-in link that opened this page signed nobody in, if it didn't. */
export function linkProblem(): string {
  return linkNote;
}

/** The service's name (JOBSEARCH_APP_NAME), once the config is in. */
export function appName(): string {
  return config?.name || "Job search";
}

export function operator(): Operator | undefined {
  return config?.operator;
}

/** The current sign-in token, refreshed when it has expired. */
export async function token(): Promise<string | null> {
  if (config?.auth === "dev") return store()?.getItem(DEV_KEY) ?? null;
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export function onChange(fn: () => void): () => void {
  if (!supabase) return () => {};
  const { data } = supabase.auth.onAuthStateChange(() => fn());
  return () => data.subscription.unsubscribe();
}

/** Where a sign-in comes back to: the page that asked, when it's the
 * consent page for the Claude connector, else the app. */
function returnTo(): string {
  return window.location.origin + (window.location.pathname === CONSENT_PATH ? CONSENT_PATH : "/");
}

export async function emailLink(email: string): Promise<void> {
  if (!supabase) throw new Error("Sign-in isn't set up on this service.");
  const { error } = await supabase.auth.signInWithOtp({
    email, options: { emailRedirectTo: returnTo() },
  });
  if (error) throw new Error(error.message);
}

export async function google(): Promise<void> {
  if (!supabase) throw new Error("Sign-in isn't set up on this service.");
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google", options: { redirectTo: returnTo() },
  });
  if (error) throw new Error(error.message);
}

export async function devSignIn(email: string): Promise<void> {
  const r = await fetch("/api/dev/sign-in", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Couldn't sign in.");
  store()?.setItem(DEV_KEY, data.token);
}

// ── the Claude connector: approving it, and taking it back ─────────────────
export const CONSENT_PATH = "/oauth/consent";
const PENDING = "switchkaro-consent";

/** The request a sign-in would lose: the link back drops the address's query. */
export function rememberConsent(): void {
  const id = new URLSearchParams(window.location.search).get("authorization_id");
  if (window.location.pathname === CONSENT_PATH && id) {
    try { store()?.setItem(PENDING, JSON.stringify({ id, at: Date.now() })); } catch { /* the query still has it */ }
  }
}

/** A request remembered in the last ten minutes - as long as Supabase keeps one. */
export function pendingConsent(): string | null {
  try {
    const p = JSON.parse(store()?.getItem(PENDING) || "null") as { id: string; at: number } | null;
    return p && Date.now() - p.at < 10 * 60_000 ? p.id : null;
  } catch {
    return null;
  }
}

export interface ConsentDetails { authorization_id: string; redirect_uri: string; client: { id: string; name: string } }

export async function consentDetails(id: string): Promise<ConsentDetails | { redirect_url: string }> {
  if (!supabase) throw new Error("Connecting apps works only on the hosted service.");
  const { data, error } = await supabase.auth.oauth.getAuthorizationDetails(id);
  if (error || !data) throw new Error(error?.message || "That request has expired - connect again from Claude.");
  return data as ConsentDetails | { redirect_url: string };
}

/** Approve or refuse, then back to the app that asked. */
export async function decideConsent(id: string, allow: boolean): Promise<void> {
  if (!supabase) throw new Error("Connecting apps works only on the hosted service.");
  const o = { skipBrowserRedirect: true };
  const { data, error } = allow ? await supabase.auth.oauth.approveAuthorization(id, o)
                                : await supabase.auth.oauth.denyAuthorization(id, o);
  if (error || !data) throw new Error(error?.message || "That didn't go through - connect again from Claude.");
  try { store()?.removeItem(PENDING); } catch { /* fine */ }
  window.location.assign(data.redirect_url);
}

export interface ConnectedApp { id: string; name: string; since: string }

export async function connectedApps(): Promise<ConnectedApp[]> {
  if (!supabase) return [];
  const { data, error } = await supabase.auth.oauth.listGrants();
  if (error || !data) return [];
  return data.map(g => ({ id: g.client.id, name: g.client.name, since: g.granted_at }));
}

export async function disconnectApp(clientId: string): Promise<void> {
  if (!supabase) return;
  const { error } = await supabase.auth.oauth.revokeGrant({ clientId });
  if (error) throw new Error(error.message);
}

export async function signOut(): Promise<void> {
  store()?.removeItem(DEV_KEY);
  if (supabase) await supabase.auth.signOut();
}
