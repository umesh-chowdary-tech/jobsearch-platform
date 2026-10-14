// What the service answers (src/jobsearch/web/routes.py and hosted/app.py).

export type Fit = "strong" | "possible" | "weak" | null;

export interface Later {
  state: "waiting" | "someday" | "due";
  until?: string | null;
  since?: string | null;
}

export interface Row {
  id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  board: string;
  verdict: string;
  fit: Fit;
  coverage: string;
  score: number;
  skills: string[];
  gaps: string[];
  closed: boolean;
  first_seen?: string | null;
  status: string;
  favorite: boolean;
  later: Later | null;
  variant: string | null;
  asks: { to: string; status: string }[];
}

export interface Today {
  first_name: string;
  decide: Row[];
  back_from_later: Row[];
  saved_for_later: number;
  favorites: number;
  unread: number;
  in_progress: Row[];
  stages: Record<string, number>;
  outreach_due: { id: string; to: string; company: string; role: string; need: string; posting_id: string }[];
  caught_up?: boolean;
}

export interface OutreachDraft {
  id: string;
  posting_id: string;
  company: string;
  role: string;
  to: string;
  how: string;
  context: string;
  note: string | null;
  message: string;
  subject: string;
  blurb: string;
  checks: { check: string; ok: boolean; level: string; detail: string }[];
  by: string;
  status: string;
  created: string;
  history: { status: string; at: string; note?: string }[];
  need?: string | null;
}

export interface Leads {
  company: string;
  leads: { who: string; why: string; how: string; url: string; employer?: string }[];
  already_asked: { to: string; status: string; role: string }[];
}

export interface ProfileEntry {
  id: string;
  title?: string | null;
  type?: string | null;
  where?: string | null;
  period?: string | null;
  headline?: string | null;
  facts?: string[] | null;
  metrics?: ({ measured?: boolean; claim?: string; estimate?: boolean } | string)[] | null;
  tech?: string[] | null;
  priority?: string | null;
  status?: string | null;
  evidence?: string | null;
  sources?: string[] | null;
  notes?: string | null;
}

export interface ProfileView {
  identity: { name?: string | null; title?: string | null; location?: string | null };
  links: Record<string, string>;
  employment: { employer: string; title?: string; location?: string; start?: string; end?: string; client?: string }[];
  languages: string[];
  entries: ProfileEntry[];
  tiers: Record<string, { skill: string; scope?: string | null }[]>;
  prefs: {
    preferred: string[]; acceptable: string[]; remote: boolean; years: number | null;
    max_years: number | null; level: string; watch: string[];
  } | null;
}

export interface Gap { skill: string; bucket: "absent" | "shallow"; required: number; nice: number }

export interface ResumeVariant {
  id: string;
  descriptor: string;
  summary: string | null;
  summary_by: string | null;
  surfaces: string[];
  created: string;
  sent_to: { posting_id: string; company?: string; role?: string; date?: string; ats?: { match: number } | null; known?: boolean }[];
  files: { name: string; url: string }[];
  checks_ok: boolean | null;
}

export interface Feedback {
  good: boolean;
  note: string;
  at: string;
}

export interface PostingDetail extends Row {
  why: string;
  judged_by: string;
  matched: Record<string, string>;
  required_gaps: string[];
  reasons: string[];
  description: string;
  live: boolean | null;
  history: { status: string; note?: string; at: string; variant?: string }[];
  files: { name: string; url: string }[];
  ats: { match: number; ceiling: number; missing: string[]; unclaimable: string[] } | null;
  feedback: Feedback | null;
  drafts: OutreachDraft[];
}

export interface Me {
  email: string;
  member: boolean;
  suspended?: boolean;
  profile_ready: boolean;
  admin: boolean;
  consent?: { version: string; at: string } | null;
  consent_current?: boolean;
}

export interface Overview {
  busy: { what: string; since?: string } | null;
  last_cycle: { at: string; line: string } | null;
  now: string;
}

export interface Job {
  id: number;
  action: string;
  label: string;
  done: boolean;
  ok: boolean | null;
  error: string;
  lines: string[];
  total_lines: number;
}

export interface JobSnapshot {
  busy: boolean;
  current: Job | null;
}

export interface SetupItem {
  id: string;
  kind: "identity" | "job" | "entry" | "fact" | "tech" | "language" | "metric" | "note";
  state: "pending" | "confirmed" | "removed";
  by?: string;
  edited?: boolean;
  source?: string;
  field?: string;
  value?: string;
  title?: string;
  employer?: string;
  location?: string;
  start?: string;
  end?: string;
  type?: string;
  where?: string;
  period?: string;
  headline?: string;
  entry?: string;
  text?: string;
  name?: string;
  sources?: { src: string; quote: string }[];
  alternatives?: ({ sources: { src: string; quote: string }[] } & Record<string, unknown>)[];
  conflict?: string;
  listed_only?: boolean;
  measured?: boolean | null;
  claim?: string;
  client?: string;
  angles?: string[];
  priority?: string;
  question?: string;
  [key: string]: unknown;
}

export interface SetupQuestion {
  id: string;
  text: string;
  why: string;
  about: string[];
  kind: string;
  options: string[];
  answer?: string;
  skipped?: boolean;
  applied?: boolean;
}

export interface SetupSource {
  id: string;
  kind: "resume" | "portfolio" | "github" | "note";
  name: string;
  lines: number;
  notes: string[];
  drafted: boolean;
  drafted_by?: string | null;
  url?: string | null;
  will_send?: string;
}

export interface Prefs {
  preferred: string[];
  acceptable: string[];
  remote: boolean;
  years: number;
  max_years: number;
  level: string;
  skip: string[];
  watch: string[];
}

export interface SetupState {
  profile: string;
  exists: boolean;
  status: "new" | "uploaded" | "drafted" | "saved";
  lines: number;
  file: string | null;
  sources: SetupSource[];
  links: { linkedin?: string; github?: string; portfolio?: string };
  items: SetupItem[];
  dropped: { what: string; why: string; source?: string; entry?: string; src?: string }[];
  progress: { confirmed: number; pending: number; removed: number; conflicts: number };
  skills: { name: string; tier: string; backed_by: number }[];
  prefs: Prefs;
  drafted_by: string | null;
  merge_notes: string[];
  interview: { questions: SetupQuestion[]; rounds: number; max_rounds: number; angles: string[]; done: boolean };
  skip_options: string[];
  limits: { files: number; pages: number };
  will_send: string;
  levels: Record<string, string>;
  result?: { entries: number; skills: number };
  /** When setup last wrote the profile; from then on every change writes it again. */
  saved_at: string | null;
  /** Why the profile can't be written from the draft as it stands, if so. */
  blocked: string;
}

export interface AdminCounts {
  ranked: number; strong: number; possible: number; resumes: number; applied: number; answers: number; good: number;
}

export interface AdminPerson {
  email: string; profile: string; joined: string | null; last_seen: string | null; suspended: string | null;
  invite: string | null; profile_ready: boolean;
  month: { requests: number; tokens: number; usd: number };
  counts: AdminCounts | null;
  own_usd_per_day: number | null;
}

export interface AdminOverview {
  people: AdminPerson[];
  active: { week: number; month: number };
  joins: { week: string; n: number }[];
  funnel: { step: string; n: number }[];
  totals: AdminCounts;
  invites_unused: { code: string; created: string; note: string }[];
  invites_used: number;
  invites_used_by: { code: string; used_at: string | null; email: string }[];
  month_usd: number;
  missing_settings?: string[];
  ai: {
    days: { day: string; usd: number; requests: number }[];
    today_usd: number; month_usd: number; forecast_usd: number;
    budget_usd: number | null; day_cap_usd: number | null;
    by_job: { job: string; tokens: number; usd: number }[];
    by_model: { model: string; requests: number; usd: number }[];
    groq_today: { requests: number; requests_cap: number | null; tokens: number; tokens_cap: number | null };
  };
  jobs: {
    days: { day: string; runs: number; failed: number; new: number }[];
    runs: number; failed: number; avg_took: number | null;
    last: { at: string; text: string; failed: boolean } | null;
    boards: { total: number; live_postings: number; stale: { name: string; last_ok: string }[]; stale_count: number };
  };
  health: {
    keys: Record<string, boolean>;
    database: { last_ok: string | null; last_error: { at: string; kind: string } | null } | null;
    cycle_paused: { since: string; until: string | null } | null;
    busy: { what: string; since?: string } | null;
    running: boolean;
    errors: { at: string; where: string; what: string }[];
  };
  settings: {
    ai_paused: boolean;
    /** Groq's free tier for every job, for a while (hosted/admin.py). */
    free_mode: { on: boolean; until: string | null; jobs: string[]; dev: boolean };
    caps: { usd_per_month: number | null; usd_per_day: number | null; person_usd_per_day: number | null;
            person_usd_per_month: number | null };
    ceiling: { usd_per_month: number; usd_per_day: number; person_usd_per_day: number; person_usd_per_month: number };
    routes: { job: string; label: string; current: string; choices: { id: string; label: string }[] }[];
  };
  log: { at: string; by: string; action: string; target: string; detail: string }[];
  requests: import("./companies").CompanyRequest[];
}
