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
}

export interface Me {
  email: string;
  member: boolean;
  profile_ready: boolean;
  admin: boolean;
  consent?: { version: string; at: string } | null;
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
  kind: "identity" | "job" | "entry" | "fact" | "tech" | "language";
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
  [key: string]: unknown;
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
  items: SetupItem[];
  dropped: { what: string; why: string; source?: string; entry?: string }[];
  progress: { confirmed: number; pending: number; removed: number };
  skills: { name: string; tier: string; backed_by: number }[];
  prefs: Prefs;
  drafted_by: string | null;
  skip_options: string[];
  will_send: string;
  levels: Record<string, string>;
  result?: { entries: number; skills: number };
}

export interface AdminOverview {
  people: { email: string; profile: string; joined: string; profile_ready: boolean;
            month: { requests: number; tokens: number; usd: number } }[];
  invites_unused: { code: string; created: string; note: string }[];
  invites_used: number;
  month_usd: number;
}
