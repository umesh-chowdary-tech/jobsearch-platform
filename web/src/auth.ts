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

export async function emailLink(email: string): Promise<void> {
  if (!supabase) throw new Error("Sign-in isn't set up on this service.");
  const { error } = await supabase.auth.signInWithOtp({
    email, options: { emailRedirectTo: window.location.origin + "/" },
  });
  if (error) throw new Error(error.message);
}

export async function google(): Promise<void> {
  if (!supabase) throw new Error("Sign-in isn't set up on this service.");
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google", options: { redirectTo: window.location.origin + "/" },
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

export async function signOut(): Promise<void> {
  store()?.removeItem(DEV_KEY);
  if (supabase) await supabase.auth.signOut();
}
