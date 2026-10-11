"use strict";
/* The job search app. No framework, no build step.

   Posting titles and descriptions come from third-party job boards, so data
   is only ever inserted as text: el() turns strings into text nodes, and a
   link is only made for http(s) URLs or the app's own paths. The one HTML
   fragment inserted as HTML is the gap chart, which the server builds with
   every value escaped - the same fragment the static dashboard uses. */

const STAGES = ["new", "shortlisted", "applied", "interviewing", "offer", "closed", "skipped"];
const HOW = [["warm", "Knows you"], ["ex-colleague", "Worked where you work"], ["alumni", "Same college"], ["cold", "No connection"]];
const MARKS = ["sent", "followed-up", "replied", "referred", "declined", "closed"];

const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");

function safeHref(u) {
  u = String(u || "");
  return /^https?:\/\//i.test(u) || u.startsWith("/files/") || u.startsWith("#/") ? u : null;
}

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "href") { const h = safeHref(v); if (h) n.setAttribute("href", h); }
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "class") n.className = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const ext = (href, text) => el("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const who = data.holder ? ` (${data.holder.what}${data.holder.since ? " since " + ago(data.holder.since) : ""})` : "";
    throw new Error((data.error || `HTTP ${r.status}`) + who);
  }
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3200);
}

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); }
  catch { toast("Copy failed - select the text instead"); }
}

function fitBadge(fit) {
  return el("span", { class: `badge ${fit || "none"}` }, fit || "unread");
}

function failBox(e) {
  return el("p", { class: "fail" }, String(e.message || e));
}

/* ── actions and the output console ─────────────────────────────────────── */

let polling = null;

async function run(action, opts = {}) {
  try {
    await api("/api/run", { action, ...opts });
  } catch (e) { toast(e.message); return; }
  openConsole();
}

/* Hide folds the output down to its title bar along the bottom instead of
   removing it: a hidden console could not be brought back until the next run. */
function setConsoleOpen(open) {
  const b = $("#console-close");
  $("#console").classList.toggle("collapsed", !open);
  document.body.classList.toggle("console-open", open);
  document.body.classList.toggle("console-min", !open);
  b.textContent = open ? "Hide" : "Show";
  b.setAttribute("aria-label", open ? "Hide output" : "Show output");
  b.setAttribute("aria-expanded", String(open));
}

function openConsole() {
  const c = $("#console");
  c.hidden = false;
  setConsoleOpen(true);
  $("#console-lines").textContent = "";
  let since = 0;
  clearInterval(polling);
  polling = setInterval(async () => {
    let s;
    try { s = await api(`/api/job?since=${since}`); } catch { return; }
    const job = s.current;
    if (!job) return;
    $("#console-title").textContent = job.label;
    if (job.lines.length) {
      const pre = $("#console-lines");
      pre.textContent += job.lines.join("\n") + "\n";
      pre.scrollTop = pre.scrollHeight;
      since = job.total_lines;
    }
    const st = $("#console-state");
    if (!job.done) {
      st.textContent = "running…"; st.className = "";
      $("#stop-run").hidden = false;
      return;
    }
    clearInterval(polling);
    st.textContent = job.ok ? "done" : `failed${job.error ? ": " + job.error : ""}`;
    st.className = job.ok ? "ok" : "fail";
    refreshHealth();
    render();
  }, 700);
}
$("#console-close").addEventListener("click", e => {
  e.stopPropagation();
  setConsoleOpen($("#console").classList.contains("collapsed"));
});
$(".console-head").addEventListener("click", () => {
  if ($("#console").classList.contains("collapsed")) setConsoleOpen(true);
});
$("#run-cycle").addEventListener("click", () => run("cycle"));

/* Stop: the run - the app's or the scheduled one - ends at its next safe
   point (between steps, boards or model requests), with what it did saved. */
$("#stop-run").addEventListener("click", async () => {
  try { await api("/api/stop", {}); toast("Stopping at the next safe point…"); }
  catch (e) { toast(e.message); }
  refreshHealth();
});

/* Pause: the scheduled cycle skips its ticks. "Run cycle" still works. */
$("#pause-for").addEventListener("change", async e => {
  const v = e.target.value;
  e.target.value = "";
  if (!v) return;
  try {
    const p = await api("/api/schedule/pause", { hours: v === "until" ? null : Number(v) });
    toast(p.until ? `Schedule paused until ${when(p.until)}` : "Schedule paused until you resume it");
  } catch (err) { toast(err.message); }
  refreshHealth();
});
$("#resume").addEventListener("click", async () => {
  try { await api("/api/schedule/resume", {}); toast("Schedule resumed"); }
  catch (e) { toast(e.message); }
  refreshHealth();
});

function when(iso) {
  const d = new Date(iso), today = new Date();
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? t
    : `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${t}`;
}

function pausedText(p) {
  return p.until ? `Schedule paused until ${when(p.until)}` : "Schedule paused until you resume it";
}

/* ── the top bar: is the watcher alive? ─────────────────────────────────── */

function span(s) {
  const m = Math.round(s / 60);
  return m < 1 ? "under a minute" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/* "Busy: ..." - counting only the time it actually ran. A cycle started before
   the PC went to sleep froze with it; its heartbeat (busy.py) says for how long. */
function busyText(b) {
  if (!b) return "";
  const head = `Busy: ${b.what}`;
  if (!b.since) return head;
  const now = Date.now();
  const beatAge = b.beat ? (now - new Date(b.beat).getTime()) / 1000 : 0;
  if (b.beat && beatAge > 120)
    return `${head} - paused since ${new Date(b.beat).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` +
      " (the PC was asleep, or it stopped responding)";
  const total = (now - new Date(b.since).getTime()) / 1000, slept = b.paused_s || 0;
  if (slept < 300) return `${head}, started ${ago(b.since)}`;
  return `${head}, running ${span(total - slept)} (started ${ago(b.since)}; the PC slept ${span(slept)} of that)`;
}

async function refreshHealth() {
  let o;
  try { o = await api("/api/overview"); } catch { $("#health").textContent = "App server not reachable"; return; }
  const bits = [];
  if (o.busy) bits.push(busyText(o.busy) + (o.stopping ? " - stopping…" : ""));
  if (o.last_cycle) bits.push(`Watcher ran ${ago(o.last_cycle.at)}`);
  else bits.push("The watcher has never run");
  if (o.paused) bits.push(pausedText(o.paused));
  else if (o.scheduler && o.scheduler["Next Run Time"]) bits.push(`next ${o.scheduler["Next Run Time"].split(" ").slice(1).join(" ")}`);
  else if (o.scheduler === null) bits.push("no schedule found");
  $("#health").textContent = bits.join(" · ");
  $("#run-cycle").disabled = Boolean(o.busy);
  $("#stop-run").hidden = !o.busy;
  $("#stop-run").disabled = Boolean(o.stopping);
  $("#stop-run").textContent = o.stopping ? "Stopping…" : "Stop";
  $("#pause-for").hidden = Boolean(o.paused);
  $("#resume").hidden = !o.paused;
}
setInterval(refreshHealth, 20000);

/* ── shared pieces ───────────────────────────────────────────────────────── */

const day = iso => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

function laterText(l) {
  if (!l) return "";
  if (l.state === "someday") return "saved for later, no date";
  if (l.state === "due") return `back from later (saved ${day(l.since)})`;
  return `saved for later until ${day(l.until)}`;
}

// Favorite: flips on the click, then saves; if the save fails it flips back,
// so the star never shows a state the server doesn't have.
function starButton(r) {
  const b = el("button", { type: "button", class: "star" });
  const paint = () => {
    b.textContent = r.favorite ? "★" : "☆";
    b.classList.toggle("on", r.favorite);
    b.setAttribute("aria-pressed", String(r.favorite));
    b.setAttribute("aria-label", r.favorite ? "Remove from favorites" : "Add to favorites");
    b.title = r.favorite ? "Favorite - click to remove" : "Add to favorites";
  };
  paint();
  b.addEventListener("click", async () => {
    r.favorite = !r.favorite;
    paint();
    try {
      await api(`/api/postings/${encodeURIComponent(r.id)}/favorite`, { on: r.favorite });
      render();
    } catch (e) { r.favorite = !r.favorite; paint(); toast(e.message); }
  });
  return b;
}

// Dates in the browser's own zone: a picked day is that day's local midnight.
const ymd = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fromToday = n => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + n); return d; };
const LATER_MAX_DAYS = 90;

// Save for later: a button that opens a calendar in place. Returns the button
// (for the row's actions) and the panel (full width, under the row).
function laterControl(r) {
  const panel = el("div", { class: "later-panel", hidden: true });
  const btn = el("button", { type: "button", "aria-expanded": "false" }, r.later ? "Later ✓" : "Later…");
  const close = () => { panel.hidden = true; btn.setAttribute("aria-expanded", "false"); btn.focus(); };
  panel.addEventListener("keydown", e => { if (e.key === "Escape") close(); });

  btn.addEventListener("click", () => {
    if (!panel.hidden) { close(); return; }
    const id = `later-${r.id}`;
    const min = ymd(fromToday(1)), max = ymd(fromToday(LATER_MAX_DAYS));
    const current = r.later && r.later.until ? ymd(new Date(r.later.until)) : ymd(fromToday(7));
    const date = el("input", { type: "date", id, min, max, value: current < min ? min : current });
    const err = el("p", { class: "fail small" });
    const send = async (body, done) => {
      err.textContent = "";
      try {
        await api(`/api/postings/${encodeURIComponent(r.id)}/later`, body);
        toast(done);
        render();
      } catch (e) { err.textContent = e.message; }
    };
    const saveDate = () => {
      if (!date.value) { err.textContent = "Pick a date first"; date.focus(); return; }
      if (date.value < min || date.value > max) {
        err.textContent = `Pick a date from tomorrow to ${day(fromToday(LATER_MAX_DAYS))}`;
        date.focus(); return;
      }
      const [y, m, d] = date.value.split("-").map(Number);
      const until = new Date(y, m - 1, d);                 // local midnight
      send({ until: until.toISOString() }, `Back on Today on ${day(until)}`);
    };
    panel.replaceChildren(
      el("label", { for: id }, "Bring it back to Today on"),
      date,
      el("div", { class: "row", style: "margin-top:6px" }, el("span", { class: "muted small" }, "or in"),
        [[3, "3 days"], [7, "1 week"], [14, "2 weeks"]].map(([n, l]) =>
          el("button", { type: "button", class: "quiet", onclick: () => { date.value = ymd(fromToday(n)); } }, l))),
      el("div", { class: "row", style: "margin-top:8px" },
        el("button", { type: "button", class: "primary", onclick: saveDate }, "Save"),
        el("button", { type: "button", onclick: () => send({ days: null }, "Saved for later, no date") }, "No date"),
        r.later && el("button", { type: "button", onclick: () => send({ clear: true }, "Back on your list") }, "Bring back now"),
        el("button", { type: "button", class: "quiet", onclick: close }, "Cancel")),
      err);
    panel.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    date.focus();
  });
  return { button: btn, panel };
}

function postingItem(r, withActions) {
  const meta = [r.coverage && `${r.coverage} backed`, r.location, r.variant && "resume ready",
                r.asks.length && `${r.asks.length} referral ask${r.asks.length > 1 ? "s" : ""}`,
                laterText(r.later)]
               .filter(Boolean).join(" · ");
  const later = withActions ? laterControl(r) : null;
  const acts = withActions ? el("div", { class: "acts" },
    el("button", { type: "button", onclick: () => setStatus(r.id, "skipped") }, "Skip"),
    later.button,
    el("button", { type: "button", onclick: () => setStatus(r.id, "shortlisted") }, "Shortlist"),
  ) : el("span", { class: "status" }, r.status);
  return el("div", { class: "item" },
    el("div", { class: "row", style: "flex-wrap:nowrap;align-items:flex-start" },
      starButton(r),
      el("div", {},
        el("div", { class: "t" }, fitBadge(r.fit), r.closed && el("span", { class: "badge closed" }, "closed"),
           el("a", { href: `#/p/${encodeURIComponent(r.id)}` }, `${r.company} · ${r.title}`)),
        el("div", { class: "m" }, meta))),
    acts,
    later && later.panel);
}

async function setStatus(pid, status, note) {
  try {
    await api(`/api/postings/${encodeURIComponent(pid)}/status`, { status, note });
    toast(`Marked ${status}`);
    render();
  } catch (e) { toast(e.message); }
}

/* ── Today ───────────────────────────────────────────────────────────────── */

async function viewToday() {
  const t = await api("/api/today");
  const chips = el("div", { class: "chips" },
    el("a", { class: "chip", href: "#/postings?flag=favorites&status=any" }, "★ favorites ", el("b", {}, t.favorites)),
    el("a", { class: "chip", href: "#/postings?flag=later" }, "saved for later ", el("b", {}, t.saved_for_later)),
    STAGES.filter(s => s !== "new").map(s =>
      el("a", { class: "chip", href: `#/postings?status=${s}` }, `${s} `, el("b", {}, t.stages[s] || 0))));
  const parts = [el("h1", {}, "Today"),
    el("p", { class: "sub" }, `${t.stages.new || 0} live postings you haven't decided on yet.`), chips];

  if (t.back_from_later.length) {
    parts.push(el("h2", {}, `Back from later · ${t.back_from_later.length}`));
    parts.push(el("div", { class: "list" }, t.back_from_later.map(r => postingItem(r, true))));
  }

  const ib = t.inbox;
  if (ib && (ib.interviews.length || ib.outcomes.length || ib.look)) {
    parts.push(el("h2", {}, "From your inbox"));
    const lines = [];
    ib.interviews.forEach(a => lines.push(el("div", { class: "item" }, el("div", {},
      el("div", { class: "t" }, statusBadge(a.status), `${a.company} · ${a.role || "role not named"}`),
      el("div", { class: "m" }, `last email ${day(a.updated)} — check it needs no reply from you`)))));
    ib.outcomes.forEach(a => lines.push(el("div", { class: "item" }, el("div", {},
      el("div", { class: "t" }, statusBadge(a.status), `${a.company} · ${a.role || "role not named"}`),
      el("div", { class: "m" }, day(a.updated))))));
    if (lines.length) parts.push(el("div", { class: "list" }, lines));
    if (ib.look) parts.push(el("p", { class: "muted" }, `${ib.look} application${ib.look > 1 ? "s" : ""} from email need a look. `,
      el("a", { href: "#/applications" }, "Open Applications")));
  }

  parts.push(el("h2", {}, `Needs a decision · ${t.decide.length}`));
  parts.push(t.decide.length
    ? el("div", { class: "list" }, t.decide.map(r => postingItem(r, true)))
    : el("p", { class: "empty" }, "Nothing strong or possible is waiting on you."));
  if (t.unread) {
    parts.push(el("p", { class: "muted" }, `${t.unread} more kept postings haven't been read by a model yet. `,
      el("button", { type: "button", class: "quiet", onclick: () => run("rank") }, "Have a model read them")));
  }
  if (t.outreach_due.length) {
    parts.push(el("h2", {}, `Referral asks · ${t.outreach_due.length}`));
    parts.push(el("div", { class: "list" }, t.outreach_due.map(d => el("div", { class: "item" },
      el("div", {}, el("div", { class: "t" }, el("a", { href: `#/p/${encodeURIComponent(d.posting_id)}` },
        `${d.to} · ${d.role} at ${d.company}`)), el("div", { class: "m" }, d.need))))));
  }
  if (t.in_progress.length) {
    parts.push(el("h2", {}, `In progress · ${t.in_progress.length}`));
    parts.push(el("div", { class: "list" }, t.in_progress.map(r => postingItem(r, false))));
  }
  if (t.escalations.length) {
    parts.push(el("h2", {}, "Needs a human check"));
    parts.push(el("div", { class: "list" }, t.escalations.map(e => el("div", { class: "item" },
      el("div", {}, el("div", { class: "t" }, `${e.provider}:${e.slug}`), el("div", { class: "m" }, e.reason)),
      el("a", { class: "chip", href: "#/system" }, "Resolve")))));
  }
  return parts;
}

/* ── Postings ────────────────────────────────────────────────────────────── */

async function viewPostings(params) {
  const rows = await api("/api/postings");
  const f = { status: params.get("status") || "open", fit: params.get("fit") || "any",
              flag: params.get("flag") || "any",
              q: params.get("q") || "", closed: params.get("closed") === "1" };
  const keep = r => {
    if (r.verdict !== "keep") return false;
    if (f.flag === "favorites" && !r.favorite) return false;
    if (f.flag === "later" && !r.later) return false;
    if (!f.closed && r.closed) return false;
    if (f.status === "open" && ["closed", "skipped"].includes(r.status)) return false;
    if (!["open", "any"].includes(f.status) && r.status !== f.status) return false;
    if (f.fit !== "any" && (r.fit || "unread") !== f.fit) return false;
    if (f.q && !`${r.company} ${r.title} ${r.location}`.toLowerCase().includes(f.q.toLowerCase())) return false;
    return true;
  };
  const shown = rows.filter(keep);
  const go = () => {
    const p = new URLSearchParams();
    if (st.value !== "open") p.set("status", st.value);
    if (fi.value !== "any") p.set("fit", fi.value);
    if (fl.value !== "any") p.set("flag", fl.value);
    if (qi.value) p.set("q", qi.value);
    if (cl.checked) p.set("closed", "1");
    location.hash = `#/postings${p.toString() ? "?" + p : ""}`;
  };
  const st = el("select", { onchange: go, "aria-label": "Status" },
    [["open", "Open (not skipped or closed)"], ["any", "Any status"], ...STAGES.map(s => [s, s])].map(
      ([v, l]) => el("option", { value: v, selected: v === f.status }, l)));
  const fi = el("select", { onchange: go, "aria-label": "Fit" },
    ["any", "strong", "possible", "weak", "unread"].map(v => el("option", { value: v, selected: v === f.fit }, v === "any" ? "Any fit" : v)));
  const fl = el("select", { onchange: go, "aria-label": "Favorites and saved" },
    [["any", "All postings"], ["favorites", "★ Favorites"], ["later", "Saved for later"]].map(
      ([v, l]) => el("option", { value: v, selected: v === f.flag }, l)));
  const qi = el("input", { type: "search", placeholder: "Company, title, place", value: f.q,
                           onchange: go, "aria-label": "Search" });
  const cl = el("input", { type: "checkbox", id: "cl", checked: f.closed, onchange: go });
  return [el("h1", {}, "Postings"),
    el("p", { class: "sub" }, `${shown.length} of ${rows.filter(r => r.verdict === "keep").length} kept postings`),
    el("div", { class: "row" }, fl, st, fi, qi, el("label", { for: "cl", class: "row" }, cl, " show closed on the board")),
    el("div", { class: "list", style: "margin-top:14px" },
      shown.length ? shown.map(r => postingItem(r, false)) : el("p", { class: "empty", style: "padding:12px 14px" }, "Nothing matches."))];
}

/* ── one posting: fit, resume, apply, referral ─────────────────────────── */

async function viewPosting(pid) {
  const p = await api(`/api/postings/${encodeURIComponent(pid)}`);
  const out = [];
  out.push(el("div", { class: "spread" },
    el("div", {},
      // The star sits beside the heading, not in it: inside, a screen reader
      // would announce the page as "Add to favorites Senior Backend...".
      el("div", { class: "row", style: "flex-wrap:nowrap;align-items:flex-start" },
        starButton(p), el("h1", {}, `${p.title} · ${p.company}`)),
      el("p", { class: "sub" }, [p.location, p.board,
        p.live === true ? "live on the last poll" : p.live === false ? "closed on the board" : "not tracked by the watcher"]
        .filter(Boolean).join(" · "))),
    fitBadge(p.fit)));

  const later = p.status === "new" ? laterControl(p) : null;
  out.push(el("div", { class: "stages", role: "group", "aria-label": "Your decision" },
    STAGES.map(s => el("button", { type: "button", class: p.status === s ? "on" : "",
                                    "aria-pressed": String(p.status === s),
                                    onclick: () => setStatus(p.id, s) }, s)),
    later && later.button));
  if (later) out.push(later.panel);
  if (p.later) out.push(el("p", { class: "muted" }, laterText(p.later)[0].toUpperCase() + laterText(p.later).slice(1) + "."));

  if (p.why) out.push(el("div", { class: "why" }, p.why, el("span", { class: "muted" }, ` — ${p.judged_by}`)));
  out.push(el("div", {},
    Object.entries(p.matched).map(([k, v]) => el("span", { class: "pill c", title: v }, k + (v === "scoped" ? "°" : ""))),
    p.required_gaps.map(g => el("span", { class: "pill g", title: "asked for, not backed" }, g))));

  // 1 · resume
  const resume = el("div", { class: "card step" }, el("h3", {}, "1 · Resume"));
  if (p.variant) {
    resume.append(el("p", { class: "small" }, `Variant `, el("b", {}, p.variant)));
    const pct = x => `${Math.round(x * 100)}%`;
    if (p.ats) resume.append(...[
      el("p", { class: "small" }, "ATS match ", el("b", {}, pct(p.ats.match)),
        el("span", { class: "muted" }, ` of what the posting asks for · your store allows at most ${pct(p.ats.ceiling)}`)),
      p.ats.missing.length && el("p", { class: "small" }, "In your store, not on this resume: ", p.ats.missing.join(", ")),
      p.ats.unclaimable.length && el("p", { class: "small muted" }, "Asked for, nothing to claim: ", p.ats.unclaimable.join(", ")),
    ].filter(Boolean));
    else resume.append(el("p", { class: "small muted" }, "Built before ATS matching - rebuild to see the match."));
    resume.append(
      el("div", { class: "row" }, p.files.map(f => ext(f.url, f.name.split(".").pop().toUpperCase()))),
      el("div", { class: "row", style: "margin-top:8px" },
        el("button", { type: "button", onclick: () => run("assemble", { posting: p.id }) }, "Rebuild")));
  } else {
    resume.append(el("p", { class: "small muted" }, "No resume built for this posting yet."),
      el("button", { type: "button", class: "primary", onclick: () => run("assemble", { posting: p.id }) }, "Build resume"));
  }

  // 2 · apply
  const apply = el("div", { class: "card step" }, el("h3", {}, "2 · Apply"),
    el("p", { class: "small muted" }, "You apply on the company's site. Nothing here submits anything."),
    el("div", { class: "row" },
      p.url && ext(p.url, "Open posting"),
      p.status !== "applied" && el("button", { type: "button", onclick: () => setStatus(p.id, "applied") }, "Mark applied")));

  // 3 · referral
  const ref = el("div", { class: "card step" }, el("h3", {}, "3 · Referral"));
  const leadsBox = el("div", {});
  ref.append(el("p", { class: "small muted" },
      p.drafts.length ? `${p.drafts.length} ask(s) drafted below.` : "Nobody asked yet."),
    el("button", { type: "button", onclick: async () => {
      leadsBox.textContent = "Loading…";
      try {
        const r = await api(`/api/postings/${encodeURIComponent(p.id)}/leads`);
        leadsBox.replaceChildren(...r.leads.map(l => el("div", { class: "lead" },
          el("div", {}, ext(l.url, l.who), el("span", { class: "muted" }, `  (${l.how})`)),
          el("div", { class: "muted small" }, l.why))));
        if (r.already_asked.length) leadsBox.append(el("p", { class: "small" },
          "Already asked here: " + r.already_asked.map(a => `${a.to} (${a.status})`).join(", ")));
      } catch (e) { leadsBox.replaceChildren(failBox(e)); }
    } }, "Find people"));
  ref.append(leadsBox);
  out.push(el("div", { class: "steps" }, resume, apply, ref));

  out.push(kitSection(p));

  // drafting
  out.push(el("h2", {}, "Referral requests"));
  if (!p.variant) {
    out.push(el("p", { class: "muted" }, "Build the resume first - a referral request shouldn't promise one that doesn't exist yet."));
  } else {
    const to = el("input", { placeholder: "Their name", "aria-label": "Their name", required: true });
    // No default. "Knows you" as the first option meant a stranger could get
    // "Hope you're doing well!" from someone who never touched the dropdown.
    const how = el("select", { "aria-label": "How you know them" },
      el("option", { value: "" }, "How do you know them?"), HOW.map(([v, l]) => el("option", { value: v }, l)));
    const ctx = el("textarea", { placeholder: "Optional: how you know them, in a line. Only this goes to the model, never their name." });
    const err = el("p", { class: "fail small" });
    const btn = el("button", { type: "button", class: "primary", onclick: async () => {
      err.textContent = "";
      if (!to.value.trim()) { err.textContent = "Enter their name first"; to.focus(); return; }
      if (!how.value) { err.textContent = "Choose how you know them"; how.focus(); return; }
      btn.disabled = true; btn.textContent = "Drafting…";
      try {
        await api("/api/outreach/draft", { posting: p.id, to: to.value, how: how.value, context: ctx.value });
        toast("Drafted");
        render();
      } catch (e) { err.textContent = e.message; btn.disabled = false; btn.textContent = "Draft"; }
    } }, "Draft");
    out.push(el("div", { class: "card stack" }, el("div", { class: "row" }, to, how), ctx, el("div", { class: "row" }, btn), err));
  }
  p.drafts.forEach(d => out.push(draftCard(d)));

  out.push(el("h2", {}, "The posting"));
  out.push(p.reasons.length ? el("p", { class: "muted" }, p.reasons.join(" · ")) : null);
  out.push(el("div", { class: "desc" }, p.description || "No description stored."));
  if (p.emails && p.emails.length) {
    out.push(el("h2", {}, "From your inbox"));
    out.push(el("div", { class: "list" }, p.emails.map(e => el("div", { class: "item" },
      el("span", {}, statusBadge(e.status), e.subject), el("span", { class: "muted" }, day(e.date))))));
  }
  if (p.history.length) {
    out.push(el("h2", {}, "History"));
    out.push(el("div", { class: "list" }, p.history.map(h => el("div", { class: "item" },
      el("span", {}, h.status, h.note ? ` - ${h.note}` : "", h.variant ? ` (resume: ${h.variant})` : ""),
      el("span", { class: "muted" }, ago(h.at))))));
  }
  return out;
}

/* ── the Apply kit: what the form asks for, ready to copy ────────────────── */

function copyRow(label, value, extra) {
  return el("div", { class: "kit-row" },
    el("span", { class: "kit-label" }, label),
    el("span", { class: "kit-value" }, value, extra || null),
    el("button", { type: "button", class: "quiet", "aria-label": `Copy ${label}`, onclick: () => copy(value) }, "Copy"));
}

function kitSection(p) {
  const body = el("div", { class: "stack" }, el("p", { class: "muted" }, "Loading…"));
  const box = el("details", { class: "card kit" },
    el("summary", {}, el("b", {}, "Apply kit"), " · everything the application form asks for, ready to copy"),
    body);
  let loaded = false;
  box.addEventListener("toggle", async () => {
    if (!box.open || loaded) return;
    loaded = true;
    // Flattened and filtered like render(): replaceChildren turns an array into
    // the text "[object HTMLHeadingElement],..." and a null into "null".
    try { body.replaceChildren(...renderKit(await api(`/api/postings/${encodeURIComponent(p.id)}/kit`)).flat(Infinity).filter(Boolean)); }
    catch (e) { body.replaceChildren(failBox(e)); loaded = false; }
  });
  return box;
}

function renderKit(k) {
  const out = [];
  const head = (t, note) => [el("h3", { class: "kit-h" }, t), note && el("p", { class: "muted small" }, note)];

  out.push(head("Experience", "If the resume parser got these wrong, overwrite them."));
  k.experience.forEach(j => out.push(el("div", {},
    copyRow("Job title", j.title), copyRow("Company", j.company), copyRow("Location", j.location),
    copyRow("From", j.from, el("span", { class: "muted" }, `  (${j.from_words})`)),
    j.current && el("div", { class: "kit-row" }, el("span", { class: "kit-label" }, "To"),
                    el("span", { class: "kit-value" }, "tick “I currently work here”")))));

  out.push(head(`Skills to add · ${k.skills_add.length}`,
    "Type each one and pick the form's match. “Seen” names were on a real Workday form; " +
    "“guess” names are a best guess - pick the closest it offers, and tell me what it was called."));
  out.push(el("div", { class: "row", style: "margin-bottom:6px" },
    el("button", { type: "button", onclick: () => copy(k.skills_add.map(s => s.workday).join(", ")) }, "Copy all (comma-separated)")));
  k.skills_add.forEach(s => out.push(copyRow(s.why === "this posting asks for it" ? "★ asked" : "resume", s.workday,
    el("span", { class: s.seen ? "badge strong" : "badge none", style: "margin-left:8px" }, s.seen ? "seen" : "guess"))));
  k.skills_add.filter(s => s.scope).forEach(s =>
    out.push(el("p", { class: "muted small" }, `${s.skill}: ${s.scope}`)));

  out.push(head("If the form suggests these", null));
  out.push(el("div", { class: "kit-cols" },
    el("div", {}, el("div", { class: "ok small" }, "Keep"),
      k.skills_keep.map(s => el("div", { class: "small" }, s.workday, el("span", { class: "muted" }, ` — ${s.why}`)))),
    el("div", {}, el("div", { class: "fail small" }, "Remove"),
      k.skills_remove.map(s => el("div", { class: "small" }, s.workday, el("span", { class: "muted" }, ` — ${s.why}`))))));
  if (k.not_backed.length) {
    out.push(el("p", { class: "small" }, el("b", {}, "Don't add, even though the posting asks: "),
      k.not_backed.join(", "), el("span", { class: "muted" }, " - nothing in your store backs them.")));
  }

  out.push(head(`Certifications · ${k.certifications.length}`, "One entry each, with Add."));
  k.certifications.forEach(c => out.push(copyRow(`${c.issuer} · ${c.year}`, c.name)));

  out.push(head("Education", null));
  k.education.forEach(ed => out.push(el("div", {},
    copyRow("School", ed.school), copyRow("Degree", ed.degree), copyRow("Field of study", ed.field),
    copyRow("From", ed.from), copyRow("To", ed.to), ed.grade && copyRow("CGPA", ed.grade))));

  out.push(head("Languages", "The form asks a proficiency for each - that's your call; the store records none."));
  k.languages.forEach(l => out.push(copyRow("Language", l)));

  out.push(head("Links", null));
  k.links.forEach(l => out.push(copyRow(l.startsWith("github") ? "GitHub" : "LinkedIn", l)));
  return out;
}

function draftCard(d) {
  const part = (label, text) => text ? el("div", { class: "draft" },
    el("div", { class: "spread" }, el("span", { class: "muted" }, label),
      el("button", { type: "button", class: "quiet", onclick: () => copy(text) }, "Copy")),
    el("pre", {}, text)) : null;
  const bad = (d.checks || []).filter(c => !c.ok);
  return el("div", { class: "card stack", style: "margin-top:12px" },
    el("div", { class: "spread" },
      el("div", {}, el("b", {}, d.to), el("span", { class: "muted" }, ` · ${d.how} · ${d.status} · written by ${d.by}`)),
      el("select", { "aria-label": "Record what you did", onchange: async ev => {
        if (!ev.target.value) return;
        try { await api(`/api/outreach/${encodeURIComponent(d.id)}/mark`, { status: ev.target.value }); toast(`Marked ${ev.target.value}`); render(); }
        catch (e) { toast(e.message); }
      } }, el("option", { value: "" }, "Record what you did…"), MARKS.map(m => el("option", { value: m }, m)))),
    bad.map(c => el("div", { class: "check-bad" }, `${c.level}: ${c.check} - ${c.detail}`)),
    part(`Connection note (${(d.note || "").length}/200)`, d.note),
    part("Message", d.message), part("Email subject", d.subject),
    part("Blurb for their referral form", d.blurb));
}

/* ── Applications: what the inbox says ─────────────────────────────────── */

const APP_STATUSES = ["approached", "applied", "update_waiting", "on_hold", "interviewing", "offer", "rejected", "cancelled"];
const STATUS_WORD = { update_waiting: "update on the portal", on_hold: "on hold", approached: "recruiter approached",
  checking: "checking" };
const statusBadge = s => el("span", { class: `badge ${
  ({ interviewing: "strong", offer: "strong", applied: "possible", rejected: "closed", cancelled: "closed" })[s] || "none"}` },
  STATUS_WORD[s] || s);

function appTimeline(a) {
  return el("details", {}, el("summary", {}, `${a.events.length} email${a.events.length > 1 ? "s" : ""}`),
    el("div", { class: "small" }, a.events.map(e => el("div", { class: "kit-row", style: "grid-template-columns:90px minmax(0,1fr) auto" },
      el("span", { class: "muted" }, day(e.date)), el("span", {}, e.subject, e.evidence ? el("div", { class: "muted" }, "“" + e.evidence + "”") : null),
      e.confidence === "low" ? el("span", { class: "muted", title: "The rules' guess - not counted until confirmed" }, statusBadge(e.status), " ?")
                             : statusBadge(e.status)))));
}

function appItem(a) {
  const meta = [a.req_id, a.source, `updated ${day(a.updated)}`].filter(Boolean).join(" · ");
  return el("div", { class: "item" },
    el("div", { style: "min-width:0" },
      el("div", { class: "t" }, statusBadge(a.status), `${a.company || "?"} · ${a.role || "role not named"}`),
      el("div", { class: "m" }, meta, a.posting_id && el("span", {}, " · ", el("a", { href: `#/p/${encodeURIComponent(a.posting_id)}` }, "tracked posting"))),
      appTimeline(a)));
}

async function appAction(id, what, body) {
  try { await api(`/api/applications/${encodeURIComponent(id)}/${what}`, body); toast("Saved"); render(); }
  catch (e) { toast(e.message); }
}

const OUTCOME_WORD = { received: "applied", interview: "interviewing", rejected: "rejected", cancelled: "cancelled",
  offer: "offer", on_hold: "on hold", inbound: "recruiter approached", portal: "update on the portal", unclear: "unclear" };
const outcomeWord = k => OUTCOME_WORD[k] || k;

/* A reading Groq could back with quotes for everything it was asked, applied
   without a click - shown with what it did and an undo. */
function autoCard(a) {
  const g = a.auto, s = g.suggestion || {};
  const did = [];
  if (s.not_an_application) did.push(["Not an application", `“${s.not_an_application.quote}”`]);
  if (s.company) did.push([`Company: ${s.company.value}`, `“${s.company.quote}”`]);
  if (s.role) did.push([`Role: ${s.role.value}`, `“${s.role.quote}”`]);
  (s.outcomes || []).forEach(o => did.push([`Email ${o.n}: ${outcomeWord(o.outcome)}`, `“${o.quote}”`]));
  return el("div", { class: "card stack", style: "margin-top:10px" },
    el("div", { class: "spread" }, el("b", {}, `${a.company || "?"} · ${a.role || "role not named"}`),
      a.ignored ? el("span", { class: "badge none" }, "not an application") : statusBadge(a.status)),
    g.summary && el("p", { class: "small", style: "margin:0" }, g.summary),
    el("ul", { class: "small" }, did.map(([what, why]) => el("li", {}, el("b", {}, what), el("span", { class: "muted" }, ` - ${why}`)))),
    el("div", { class: "row" },
      el("span", { class: "muted small" }, `${g.by} · ${ago(g.at)}`),
      el("button", { type: "button", class: "quiet", onclick: () => appAction(a.id, "undo-auto", {}) }, "Undo")));
}

/* Groq's reading of a flagged record: every claim with the words it quoted,
   what was dropped for lack of a quote, and exactly what was sent. */
function groqBox(a, all) {
  const s = a.suggestion;
  const label = id => { const o = all.find(x => x.id === id); return o ? `${o.company || "?"} · ${o.role || "no role"} (${o.status})` : id; };
  const said = [];
  if (s.not_an_application) said.push(["Not an application", `“${s.not_an_application.quote}”`]);
  if (s.company) said.push([`Company: ${s.company.value}`, `“${s.company.quote}”`]);
  if (s.role) said.push([`Role: ${s.role.value}`, `“${s.role.quote}”`]);
  (s.outcomes || []).forEach(o => said.push([`Email ${o.n}: ${outcomeWord(o.outcome)}`, `“${o.quote}”`]));
  if (s.same_as) said.push([`Same job as ${label(s.same_as.id)}`, s.same_as.why]);
  const sent = el("div", { class: "small" }, "Loading…");
  const shown = el("details", { ontoggle: async ev => {
      if (!ev.target.open || sent.dataset.done) return;
      sent.dataset.done = "1";
      try {
        const rows = await api(`/api/applications/${encodeURIComponent(a.id)}/sent`);
        sent.replaceChildren(...(rows.length ? rows.map(r => el("div", { class: "stack" },
          el("div", { class: "muted" }, `${ago(r.at)} · ${r.chars} characters to ${r.to} · sha256 ${r.sha256.slice(0, 12)}…`),
          r.payload ? el("pre", {}, r.payload) : el("div", { class: "muted" }, "The text was deleted once answered; only this line is kept.")))
          : [el("span", { class: "muted" }, "Nothing on record.")]));
      } catch (e) { sent.textContent = e.message; }
    } }, el("summary", {}, "What was sent to Groq"), sent);
  return el("div", { class: "groq stack" },
    el("div", { class: "kit-h", style: "margin-top:0" }, `Groq's reading · ${s.by}`),
    s.summary && el("p", { class: "small", style: "margin:0" }, s.summary),
    said.length ? el("ul", { class: "small" }, said.map(([what, why]) => el("li", {}, el("b", {}, what), why ? el("span", { class: "muted" }, ` - ${why}`) : null)))
                : el("p", { class: "muted small" }, "It found nothing it could quote from the emails."),
    s.dropped && s.dropped.length ? el("p", { class: "muted small" }, "Left out, because Groq couldn't show it in the email: " + s.dropped.join("; ")) : null,
    el("div", { class: "row" },
      said.length ? el("button", { type: "button", class: "primary", onclick: () => appAction(a.id, "suggestion", { use: true }) }, "Use Groq's reading") : null,
      el("button", { type: "button", class: "quiet", onclick: () => appAction(a.id, "suggestion", { use: false }) }, "Dismiss")),
    shown);
}

async function ruleAction(id, what) {
  try { await api(`/api/mail-rules/${encodeURIComponent(id)}/${what}`, {}); toast(what === "approve" ? "Rule approved" : "Rule rejected"); render(); }
  catch (e) { toast(e.message); }
}

function ruleCard(p) {
  const n = p.changes.length;
  const rule = p.type === "phrase"
    ? [el("b", {}, `“${p.phrase}”`), " means ", el("b", {}, outcomeWord(p.means))]
    : ["Emails from ", el("b", {}, p.sender), " are from ", el("b", {}, p.company)];
  return el("div", { class: "card stack", style: "margin-top:10px" },
    el("div", {}, ...rule),
    el("div", { class: "muted small" }, `Learned from ${p.learned_from.company || "an email"}: “${p.learned_from.subject}” (${p.learned_from.date})`),
    p.example && el("div", { class: "small" }, "In the email: ", el("span", { class: "muted" }, `“${p.example}”`)),
    el("div", { class: "small" }, p.type === "phrase"
      ? `Replayed on your last ${p.replayed} job emails. It would change ${n}:`
      : `Checked against your applications. It would rename ${n}${n ? ":" : "."}`),
    n ? el("ul", { class: "small" }, p.changes.map(c => el("li", {}, p.type === "phrase"
        ? `${c.date} · ${c.company} · ${c.subject}: ${outcomeWord(c.from)}${c.from_confidence === "low" ? " (unsure)" : ""} → ${outcomeWord(c.to)}${c.from === c.to ? " (sure)" : ""}`
        : `${c.date} · ${c.subject}: ${c.from || "no company"} → ${c.to}`))) : null,
    el("div", { class: "row" },
      el("button", { type: "button", class: "primary", onclick: () => ruleAction(p.id, "approve") }, "Approve"),
      el("button", { type: "button", class: "quiet", onclick: () => ruleAction(p.id, "reject") }, "Reject")));
}

function lookCard(a, all) {
  const co = el("input", { value: a.company || "", placeholder: "Company", "aria-label": "Company" });
  const ro = el("input", { value: a.role || "", placeholder: "Role", "aria-label": "Role" });
  const others = all.filter(x => x.id !== a.id).sort((x, y) =>
    ((y.company || "").toLowerCase() === (a.company || "").toLowerCase()) - ((x.company || "").toLowerCase() === (a.company || "").toLowerCase()));
  return el("div", { class: "card stack", style: "margin-top:10px" },
    el("div", { class: "spread" }, el("b", {}, `${a.company || "?"} · ${a.role || "role not named"}`), statusBadge(a.status)),
    el("ul", { class: "small" }, a.look.map(r => el("li", {}, r))),
    a.suggestion ? groqBox(a, all) : null,
    el("div", { class: "row" }, co, ro,
      el("button", { type: "button", class: "primary", onclick: () => appAction(a.id, "confirm", { company: co.value, role: ro.value }) }, "Looks right")),
    el("div", { class: "row" },
      el("select", { "aria-label": "Set the status", onchange: ev => ev.target.value && appAction(a.id, "status", { status: ev.target.value }) },
        el("option", { value: "" }, "Set status…"), APP_STATUSES.map(s => el("option", { value: s }, STATUS_WORD[s] || s))),
      el("select", { "aria-label": "Same application as", onchange: ev => ev.target.value && appAction(a.id, "merge", { into: ev.target.value }) },
        el("option", { value: "" }, "Same as…"), others.map(o => el("option", { value: o.id }, `${o.company || "?"} · ${o.role || "no role"} (${o.status})`))),
      el("button", { type: "button", class: "quiet", onclick: () => appAction(a.id, "ignore", { ignored: true }) }, "Not an application")),
    appTimeline(a));
}

async function viewApplications() {
  const d = await api("/api/applications");
  const out = [el("h1", {}, "Applications"),
    el("p", { class: "sub" }, d.configured
      ? `Read from your inbox, read-only${d.last_sync ? ` · last checked ${ago(d.last_sync)}` : " · not checked yet"}. Nothing is ever sent, marked read or moved.`
      : "The inbox isn't connected. Add GMAIL_USER and GMAIL_APP_PASSWORD to .env - see docs/mail.md."),
    el("div", { class: "row" }, el("button", { type: "button", onclick: () => run("mail") }, "Check the inbox now")),
    el("div", { class: "chips" }, [...APP_STATUSES, "checking"].filter(s => d.counts[s]).map(s =>
      el("span", { class: "chip" }, `${STATUS_WORD[s] || s} `, el("b", {}, d.counts[s]))))];
  if (d.proposals.length) {
    out.push(el("h2", {}, `Rules waiting for you · ${d.proposals.length}`));
    out.push(el("p", { class: "muted" }, "Groq suggested these from emails it read. Each was replayed against your past mail first - this is everything it would change. Nothing is applied until you approve."));
    d.proposals.forEach(p => out.push(ruleCard(p)));
  }
  if (d.look.length) {
    out.push(el("h2", {}, `Needs a look · ${d.look.length}`));
    out.push(el("p", { class: "muted" }, d.groq
      ? "The rules weren't sure about these, so they show as “checking” rather than a guess. Groq reads each one on the next inbox check. When it can quote the email for every answer, the answer is applied and listed under “Settled by Groq”, where you can undo it. Otherwise it waits here for you."
      : "The rules weren't sure about these. Fix what's wrong, or say it looks right. (With GROQ_API_KEY in .env, Groq would suggest answers.)"));
    if (d.groq && d.groq_last && d.groq_last.skipped)
      out.push(el("p", { class: "check-bad" }, `Groq couldn't be used at the last check (${ago(d.groq_last.at)}): ${d.groq_last.skipped}. Put a new key in .env as GROQ_API_KEY - nothing is sent until it works.`));
    d.look.forEach(a => out.push(lookCard(a, d.apps)));
  }
  if (d.auto && d.auto.length) {
    out.push(el("h2", {}, `Settled by Groq · ${d.auto.length}`));
    out.push(el("p", { class: "muted" }, "Applied without a click in the last two weeks: Groq quoted the email for everything it was asked. Undo puts a record back as it was, with Groq's reading waiting for you."));
    d.auto.forEach(a => out.push(autoCard(a)));
  }
  out.push(el("h2", {}, `All applications · ${d.apps.length}`));
  out.push(d.apps.length ? el("div", { class: "list" }, d.apps.map(appItem))
                         : el("p", { class: "empty" }, "None yet - check the inbox."));
  const n = d.naukri;
  if (n.applied_all) {
    out.push(el("h2", {}, "Naukri"));
    out.push(el("div", { class: "card small" },
      `${n.applied_30d} applies in the last 30 days, across ${n.days_30d} days`,
      n.recruiter_actions_30d ? ` · ${n.recruiter_actions_30d} recruiter actions` : "",
      n.status_pings ? ` · ${n.status_pings} status changes only visible on Naukri` : "",
      el("p", { class: "muted" }, "Counted, not listed: Naukri's emails name one job per day and fill the rest with recommendations. The list is on Naukri itself.")));
  }
  return out;
}

/* ── Resumes, Outreach, Market ─────────────────────────────────────────── */

async function viewResumes() {
  const r = await api("/api/resumes");
  return [el("h1", {}, "Resumes"),
    el("p", { class: "sub" }, "Each posting gets the best-matching resume the store allows; an existing one is reused only when nothing fresh beats it. PDFs are checked on their own extracted text."),
    r.variants.map(v => el("div", { class: "card stack", style: "margin-top:12px" },
      el("div", { class: "spread" }, el("b", {}, `${v.id} · ${v.descriptor}`),
        el("span", { class: v.checks_ok === false ? "fail small" : "muted small" },
          v.checks_ok === null ? "not checked" : v.checks_ok ? "parse checks pass" : "parse checks FAIL")),
      v.summary && el("p", { class: "small" }, v.summary, el("span", { class: "muted" }, ` - ${v.summary_by}`)),
      el("div", {}, v.surfaces.map(s => el("span", { class: "pill" }, s))),
      el("div", { class: "row" }, v.files.map(f => ext(f.url, f.name))),
      el("p", { class: "muted small" }, "Used for: " + (v.sent_to.map(s => `${s.role} (${s.date})`).join("; ") || "nothing yet"))
    )),
    el("h2", {}, "Hand-written"),
    r.manual.length ? el("div", { class: "card row" }, r.manual.map(f => ext(f.url, f.name)))
                    : el("p", { class: "empty" }, "None.")];
}

async function viewOutreach() {
  const rows = await api("/api/outreach");
  return [el("h1", {}, "Outreach"),
    el("p", { class: "sub" }, "Every referral ask. You send; this only drafts and remembers."),
    rows.length ? el("div", { class: "list" }, rows.map(d => el("div", { class: "item" },
      el("div", {}, el("div", { class: "t" }, el("a", { href: `#/p/${encodeURIComponent(d.posting_id)}` }, `${d.to} · ${d.role} at ${d.company}`)),
        el("div", { class: "m" }, `${d.how} · ${d.status}`)),
      el("span", { class: d.need ? "status" : "muted small" }, d.need || "nothing due"))))
    : el("p", { class: "empty" }, "No asks yet. Open a posting and use Find people.")];
}

async function viewMarket() {
  const m = await api("/api/market");
  const card = el("div", { class: "card" });
  card.innerHTML = m.chart_html;          // server-built, every value escaped
  return [el("h1", {}, "Market"),
    el("p", { class: "sub" }, "What kept postings ask for that nothing in your store backs. What the market asks for most, which isn't the same as what makes you worth hiring."),
    card];
}

/* ── System ──────────────────────────────────────────────────────────────── */

async function viewSystem() {
  const s = await api("/api/system");
  const sched = s.scheduler;
  const search = el("input", { placeholder: "Postman, Zepto", "aria-label": "Companies" });
  const out = [el("h1", {}, "System"),
    el("p", { class: "sub" }, s.busy ? busyText(s.busy) : "Idle"),
    el("div", { class: "card stack" },
      el("div", {}, el("b", {}, "Schedule: "), sched ? `${sched.Status} · last ${sched["Last Run Time"]} (result ${sched["Last Result"]}) · next ${sched["Next Run Time"]}` : "no scheduled task found",
        s.paused ? ` · ${pausedText(s.paused)}` : ""),
      el("div", {}, el("b", {}, "Models: "), s.models.map(m => `${m.label} ${m.configured ? "ready" : "no key"}`).join(" · ")),
      el("div", {}, el("b", {}, "PDF engine: "), s.tex_engine ? "Tectonic installed" : "not installed - resumes build without a PDF")),
    el("h2", {}, "Run"),
    el("div", { class: "row" },
      [["cycle", "Full cycle"], ["watch", "Watch"], ["triage", "Triage"], ["rank", "Rank"], ["verify", "Verify boards"], ["dashboard", "Static dashboard"]]
        .map(([a, l]) => el("button", { type: "button", onclick: () => run(a) }, l))),
    el("div", { class: "row", style: "margin-top:10px" }, search,
      el("button", { type: "button", onclick: () => search.value.trim() ? run("search", { companies: search.value }) : toast("Enter a company name") }, "Look for companies")),
  ];

  out.push(el("h2", {}, `Needs a human check · ${s.escalations.length}`));
  out.push(s.escalations.length ? s.escalations.map(e => {
    const note = el("input", { placeholder: "What you checked, e.g. 'Everpure is Pure Storage's new name - confirmed on their site'", "aria-label": "Resolution note", style: "flex:1;min-width:260px" });
    return el("div", { class: "card stack" },
      el("div", {}, el("b", {}, `${e.provider}:${e.slug}`), el("span", { class: "muted" }, ` · ${e.kind} · raised ${ago(e.raised_at)}`)),
      el("div", { class: "small" }, e.reason),
      el("div", { class: "muted small" }, Object.entries(e.evidence || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")),
      el("div", { class: "row" }, note, el("button", { type: "button", onclick: async () => {
        if (!note.value.trim()) { toast("Say what you checked first"); note.focus(); return; }
        try { await api("/api/escalations/resolve", { provider: e.provider, slug: e.slug, note: note.value }); toast("Resolved"); render(); }
        catch (err) { toast(err.message); }
      } }, "Resolve")));
  }) : el("p", { class: "empty" }, "Nothing waiting."));

  out.push(el("h2", {}, "Recent cycles"));
  out.push(el("div", { class: "list" }, s.log.map(l => el("div", { class: "item" },
    el("span", { class: "small" }, l.line), el("span", { class: "muted small" }, ago(l.at))))));

  out.push(el("h2", {}, `Boards · ${s.boards.length}`));
  out.push(el("div", { class: "card scroll-x" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Board", "Name", "Live", "Polls", "Last checked"].map(h => el("th", {}, h)))),
    el("tbody", {}, s.boards.map(b => el("tr", {},
      el("td", {}, b.key), el("td", {}, b.name || "—"), el("td", {}, b.live), el("td", {}, b.polls || "not polled"),
      el("td", {}, b.last_ok ? ago(b.last_ok) : "—")))))));
  return out;
}

/* ── Profile: the store, read-only ─────────────────────────────────────── */

async function viewProfile() {
  const p = await api("/api/profile");
  const tierNames = { core: "Core", working: "Working", scoped: "Scoped (narrower than the word)",
                      incidental: "Incidental (never under Skills)", not_a_skill: "Not a skill", banned: "Banned" };
  return [el("h1", {}, p.identity.name),
    el("p", { class: "sub" }, `${p.identity.title} · ${p.identity.location} · edit this profile's master-store.yaml to change anything here`),
    el("h2", {}, "Skills"),
    Object.entries(tierNames).filter(([k]) => p.tiers[k]).map(([k, label]) => el("div", { style: "margin-bottom:10px" },
      el("div", { class: "muted small" }, `${label} · ${p.tiers[k].length}`),
      p.tiers[k].map(s => el("span", { class: "pill" + (k === "core" ? " c" : ""), title: s.scope || "" }, s.skill)))),
    el("h2", {}, `Entries · ${p.entries.length}`),
    p.entries.map(e => el("div", { class: "card stack", style: "margin-top:10px" },
      el("div", { class: "spread" }, el("b", {}, e.title || e.headline),
        el("span", { class: "muted small" }, [e.type, e.where, e.period, e.priority === "low" && "cut first"].filter(Boolean).join(" · "))),
      e.title && el("div", { class: "small" }, e.headline),
      el("ul", { class: "small" }, (e.facts || []).map(f => el("li", {}, f)),
        (e.metrics || []).filter(m => m.measured).map(m => el("li", {}, el("b", {}, "measured: "), m.claim))),
      el("div", {}, (e.tech || []).map(t => el("span", { class: "pill" }, t)))))];
}

/* ── Setup: a profile from a resume ────────────────────────────────────────
   The AI drafts; every item shows the resume line it came from; nothing is
   in the profile until it is confirmed here. */

const SETUP_TYPES = { ".pdf": "application/pdf", ".txt": "text/plain",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
const TIER_LABELS = [["core", "Strong - lead with it"], ["working", "Used it"],
                     ["incidental", "Touched it - never listed"], ["remove", "Not a skill of mine"]];
const ENTRY_KINDS = [["work", "Work"], ["project", "Projects"], ["education", "Education"],
                     ["certification", "Certifications"], ["award", "Awards"]];
const IDENTITY_NAMES = { name: "Name", location: "Location", email: "Email", phone: "Phone",
                         linkedin: "LinkedIn", github: "GitHub" };

async function setupStep(step, body) {
  try { await api(`/api/setup/${step}`, body || {}); render(); }
  catch (e) { toast(e.message); }
}

async function uploadResume(file, button) {
  const ext = ((file.name || "").match(/\.[^.]+$/) || [""])[0].toLowerCase();
  if (!SETUP_TYPES[ext]) { toast("Upload a PDF, a Word file (.docx) or a plain text file"); return; }
  button.disabled = true; button.textContent = "Reading…";
  try {
    const r = await fetch("/api/setup/upload", { method: "POST", headers: { "Content-Type": SETUP_TYPES[ext] }, body: file });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    render();
  } catch (e) { toast(e.message); button.disabled = false; button.textContent = "Upload"; }
}

function setupUpload(s) {
  const input = el("input", { type: "file", accept: ".pdf,.docx,.txt", "aria-label": "Your resume" });
  const btn = el("button", { type: "button", class: s.status === "new" ? "primary" : "" }, "Upload");
  btn.addEventListener("click", () => input.files[0] ? uploadResume(input.files[0], btn) : toast("Choose a file first"));
  return el("div", { class: "card stack" },
    el("div", { class: "kit-h", style: "margin-top:0" }, "1 · Your resume"),
    el("p", { class: "small" }, "A PDF, a Word file or plain text. It stays on this computer; only its text, with your email, phone and links removed, is sent to the AI."),
    s.file && el("p", { class: "muted small" }, `Read: ${s.lines} lines from ${s.file}. Upload again to start over.`),
    el("div", { class: "row" }, input, btn));
}

function setupDraftCard(s) {
  const btn = el("button", { type: "button", class: "primary" }, "Draft my facts");
  btn.addEventListener("click", async () => {
    btn.disabled = true; btn.textContent = "Drafting - about a minute…";
    await setupStep("draft");
  });
  return el("div", { class: "card stack" },
    el("div", { class: "kit-h", style: "margin-top:0" }, "2 · Let the AI draft your facts"),
    el("p", { class: "small" }, "One request, counted against your daily AI limit. Every item it returns must quote a line of your resume, or it is left out."),
    el("details", {}, el("summary", {}, "Exactly what will be sent"), el("pre", { class: "send" }, s.will_send || "")),
    btn);
}

function setupItem(it, fields) {
  const inputs = {};
  const boxes = fields.map(([key, label, multi]) => {
    const box = multi ? el("textarea", { rows: 2 }, it[key] || "") : el("input", { type: "text", value: it[key] || "" });
    inputs[key] = box;
    return el("label", { class: multi ? "wide" : "" }, label || "", box);
  });
  const act = state => {
    const changed = {};
    for (const [k, box] of Object.entries(inputs)) if (box.value !== (it[k] || "")) changed[k] = box.value;
    setupStep("item", { id: it.id, state, fields: changed });
  };
  const from = it.source ? el("div", { class: "src" }, "From your resume: ", el("q", {}, it.source))
    : el("div", { class: "src" }, it.by === "you" ? "Written by you" : "");
  return el("div", { class: `setup-item ${it.state}` }, el("div", { class: "fields" }, boxes), from,
    el("div", { class: "row" },
      it.state !== "confirmed" && el("button", { type: "button", class: "primary", onclick: () => act("confirmed") }, "Confirm"),
      it.state !== "removed" && el("button", { type: "button", onclick: () => act("removed") }, "Remove"),
      it.state !== "pending" && el("button", { type: "button", onclick: () => act("pending") }, "Undo"),
      el("span", { class: `state ${it.state}` }, it.state === "pending" ? "to check" : it.state),
      it.edited && el("span", { class: "muted small" }, "edited by you")));
}

function techChips(items) {
  if (!items.length) return null;
  const pending = items.filter(t => t.state === "pending");
  return el("div", { class: "stack" },
    el("div", { class: "muted small" }, "Skills this work names - each from its line:"),
    el("div", {}, items.map(t => el("span", { class: `chip ${t.state}`, title: t.source },
      t.name,
      t.state !== "confirmed" && el("button", { type: "button", "aria-label": `Confirm ${t.name}`, onclick: () => setupStep("item", { id: t.id, state: "confirmed" }) }, "✓"),
      t.state !== "removed" && el("button", { type: "button", "aria-label": `Remove ${t.name}`, onclick: () => setupStep("item", { id: t.id, state: "removed" }) }, "✕")))),
    pending.length > 1 && el("button", { type: "button", onclick: async () => {
      for (const t of pending) await api("/api/setup/item", { id: t.id, state: "confirmed" }).catch(e => toast(e.message));
      render();
    } }, `Confirm all ${pending.length} skills here`));
}

function setupEntry(e, items) {
  const facts = items.filter(i => i.kind === "fact" && i.entry === e.id);
  const tech = items.filter(i => i.kind === "tech" && i.entry === e.id);
  const add = el("input", { type: "text", placeholder: "Add a fact in your own words (numbers only if they are true)" });
  return el("div", { class: `card stack entry ${e.state}` },
    setupItem(e, [["title", "Name"], ["where", "Where"], ["period", "When"], ["headline", "What it was", true]]),
    e.state !== "removed" && el("div", { class: "stack facts" },
      facts.map(f => setupItem(f, [["text", "", true]])),
      el("div", { class: "row" }, add,
        el("button", { type: "button", onclick: () => setupStep("fact", { entry: e.id, text: add.value }) }, "Add")),
      techChips(tech)));
}

function setupReview(s) {
  const items = s.items, p = s.progress;
  const of = kind => items.filter(i => i.kind === kind);
  const out = [el("div", { class: "kit-h" }, "2 · Drafted" + (s.drafted_by ? ` by ${s.drafted_by}` : "")),
    el("p", { class: "muted small" }, "Upload the resume again to start over from a new draft."),
    el("div", { class: "kit-h" }, "3 · Check every item"),
    el("p", { class: "small" }, `${p.confirmed} confirmed · ${p.pending} still to check · ${p.removed} removed. Only confirmed items go into your profile. Edit anything before confirming - your own words stand as you write them.`),
  ];
  const ids = of("identity");
  if (ids.length) out.push(el("h2", {}, "About you"),
    el("div", { class: "card stack" }, ids.map(i => setupItem(i, [["value", IDENTITY_NAMES[i.field] || i.field]]))));
  const jobs = of("job");
  if (jobs.length) out.push(el("h2", {}, "Jobs"), el("div", { class: "card stack" },
    jobs.map(j => setupItem(j, [["title", "Title"], ["employer", "Employer"], ["location", "Location"], ["start", "From (YYYY-MM)"], ["end", "To (YYYY-MM or present)"]]))));
  for (const [kind, label] of ENTRY_KINDS) {
    const entries = of("entry").filter(e => e.type === kind);
    if (entries.length) out.push(el("h2", {}, label), entries.map(e => setupEntry(e, items)));
  }
  const langs = of("language");
  if (langs.length) out.push(el("h2", {}, "Languages"), el("div", { class: "card stack" }, langs.map(l => setupItem(l, [["value", ""]]))));
  if (s.dropped.length) out.push(el("h2", {}, `Left out · ${s.dropped.length}`),
    el("p", { class: "small" }, "The AI said these, but they didn't match your resume, so they were not kept."),
    el("div", { class: "card stack" }, s.dropped.map((d, n) => el("div", { class: "spread" },
      el("div", {}, el("div", { class: "small" }, d.what), el("div", { class: "muted small" }, d.why),
        d.source && el("div", { class: "src" }, el("q", {}, d.source))),
      d.entry && d.source && el("button", { type: "button", onclick: () => setupStep("restore", { index: n }) }, "Use the resume line")))));
  return out;
}

function setupSkills(s) {
  if (!s.skills.length) return el("div", { class: "card" }, el("div", { class: "kit-h", style: "margin-top:0" }, "4 · Skills"),
    el("p", { class: "muted small" }, "Confirm some work and the skills its lines name, and they appear here."));
  const picks = {};
  const rows = s.skills.map(k => {
    const sel = el("select", { "aria-label": `How well you know ${k.name}` },
      TIER_LABELS.map(([v, l]) => el("option", { value: v, selected: v === k.tier }, l)));
    picks[k.name] = sel;
    return el("tr", {}, el("td", {}, k.name), el("td", { class: "muted small" }, `${k.backed_by} entr${k.backed_by === 1 ? "y" : "ies"}`), el("td", {}, sel));
  });
  return el("div", { class: "card stack" },
    el("div", { class: "kit-h", style: "margin-top:0" }, "4 · Skills"),
    el("p", { class: "small" }, "Only skills your confirmed work names. \"Touched it\" is never listed on a resume."),
    el("table", {}, el("tbody", {}, rows)),
    el("button", { type: "button", onclick: () => setupStep("skills", {
      tiers: Object.fromEntries(Object.entries(picks).map(([n, sel]) => [n, sel.value])) }) }, "Save skill choices"));
}

function setupPrefs(s) {
  const p = s.prefs || {};
  const csv = v => (v || []).join(", ");
  const preferred = el("input", { type: "text", value: csv(p.preferred) });
  const acceptable = el("input", { type: "text", value: csv(p.acceptable) });
  const remote = el("input", { type: "checkbox", checked: p.remote !== false });
  const years = el("input", { type: "number", min: "0", max: "50", value: p.years ?? 0 });
  const maxYears = el("input", { type: "number", min: "0", max: "60", value: p.max_years ?? 2 });
  const level = el("select", {}, Object.entries(s.levels).map(([k, l]) => el("option", { value: k, selected: k === p.level }, l)));
  const skip = Object.fromEntries(s.skip_options.map(k => [k, el("input", { type: "checkbox", checked: (p.skip || []).includes(k) })]));
  const watch = el("input", { type: "text", value: csv(p.watch) });
  const save = el("button", { type: "button", class: "primary" }, s.exists ? "Save and replace my profile" : "Save my profile");
  save.addEventListener("click", async () => {
    if (s.progress.pending && !confirm(`${s.progress.pending} item(s) are not confirmed and will be left out. Save anyway?`)) return;
    if (s.exists && !confirm("Replace the saved profile with this one?")) return;
    const prefs = { preferred: preferred.value, acceptable: acceptable.value, remote: remote.checked,
      years: Number(years.value), max_years: Number(maxYears.value), level: level.value,
      skip: Object.entries(skip).filter(([, b]) => b.checked).map(([k]) => k), watch: watch.value };
    try {
      const r = await api("/api/setup/save", { prefs, replace: s.exists });
      toast(`Saved: ${r.result.entries} entries, ${r.result.skills} skills`);
      location.hash = "#/"; location.reload();
    } catch (e) { toast(e.message); }
  });
  return el("div", { class: "card stack" },
    el("div", { class: "kit-h", style: "margin-top:0" }, "5 · What you're looking for"),
    el("label", {}, "Cities you'd most like (comma-separated)", preferred),
    el("label", {}, "Also fine - cities or a country", acceptable),
    el("label", { class: "inline" }, remote, " Remote roles are fine"),
    el("div", { class: "row" }, el("label", {}, "Years of experience", years), el("label", {}, "Skip roles asking more than (years)", maxYears)),
    el("label", {}, "You are", level),
    el("div", {}, el("div", { class: "muted small" }, "Roles to skip"),
      s.skip_options.map(k => el("label", { class: "inline" }, skip[k], ` ${k}`))),
    el("label", {}, "Companies you'd like watched (comma-separated)", watch),
    save);
}

async function viewSetup() {
  const s = await api("/api/setup");
  const out = [el("h1", {}, "Set up from your resume"),
    el("p", { class: "sub" }, `Profile "${s.profile}" · it will be saved in ${s.dir}`)];
  if (s.exists) out.push(el("div", { class: "card" }, "Your profile is saved. ",
    el("a", { href: "#/" }, "See today's jobs"), ", or upload a resume to set it up again."));
  out.push(setupUpload(s));
  if (s.status === "uploaded") out.push(setupDraftCard(s));
  if (s.status === "drafted" || s.status === "saved") out.push(setupReview(s), setupSkills(s), setupPrefs(s));
  return out;
}

/* Before a profile exists, setup is the only screen there is. */
async function setupGate() {
  try {
    const s = await api("/api/setup");
    document.body.classList.toggle("setup-only", !s.exists);
    if (!s.exists && !location.hash.startsWith("#/setup")) location.hash = "#/setup";
  } catch { /* the rest of the app reports its own errors */ }
}

/* ── router ──────────────────────────────────────────────────────────────── */

async function render() {
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query || "");
  const seg = (path || "/").split("/").filter(Boolean);
  const routes = { "": viewToday, postings: viewPostings, applications: viewApplications, resumes: viewResumes, outreach: viewOutreach,
                   market: viewMarket, system: viewSystem, profile: viewProfile, setup: viewSetup };
  let fn, name = seg[0] || "";
  if (name === "p" && seg[1]) { fn = () => viewPosting(decodeURIComponent(seg[1])); name = "postings"; }
  else fn = routes[name] || viewToday;
  document.querySelectorAll(".side a").forEach(a =>
    a.classList.toggle("on", a.dataset.view === (name || "today")));
  try {
    const nodes = await fn(params);
    view.replaceChildren(...[nodes].flat(Infinity).filter(Boolean));
  } catch (e) {
    view.replaceChildren(el("h1", {}, "Something went wrong"), failBox(e));
  }
}

window.addEventListener("hashchange", () => { render(); view.focus({ preventScroll: true }); window.scrollTo(0, 0); });
refreshHealth();
setupGate().then(render);
api("/api/job").then(s => { if (s.busy) openConsole(); }).catch(() => {});
