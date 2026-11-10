// The overall resume: built from the whole profile, in the person's own style.
// This is where they say how they like their resume - length, projects per
// job, bullets, which sections and in what order. Job resumes are built for
// one job; this one is never sent, so rebuilding replaces it.

import { useCallback, useEffect, useRef, useState } from "react";
import { blob, openFile, post } from "../api";
import { useJob } from "../jobs";
import type { BaseResume as Base, ResumeStyle, StyleView } from "../types";
import { day, Icon, useToast } from "../ui";

const FILE_NAMES: Record<string, string> = { pdf: "PDF", html: "Web page", txt: "Plain text", tex: "LaTeX" };
const SECTION_NAMES: Record<string, string> = {
  summary: "Summary", skills: "Skills", experience: "Experience", projects: "Side projects",
  education: "Education", certifications: "Certifications", achievements: "Achievements", languages: "Languages",
};
const NUMBERS: [keyof ResumeStyle, string][] = [
  ["projects_per_job", "Projects per job"], ["pieces_per_project", "Pieces under a project"],
  ["project_bullets", "Bullets per project"], ["piece_bullets", "Bullets per piece"],
  ["side_projects", "Side projects"],
];

export function BaseResume({ base, style, hasProfile, onChange }: {
  base: Base | null; style: StyleView; hasProfile: boolean; onChange: () => void;
}) {
  const toast = useToast();
  const build = useJob(onChange);
  const [busy, setBusy] = useState(false);
  const rebuild = useCallback(() => build.start("base"), [build]);

  const change = async (changes: Partial<Record<keyof ResumeStyle, unknown>>) => {
    setBusy(true);
    try { await post("/api/resumes/style", { changes }); onChange(); if (base) rebuild(); }
    catch (e) { toast((e as Error).message); }
    finally { setBusy(false); }
  };

  if (!hasProfile) {
    return (
      <div className="card stack">
        <p><b>Your resume</b></p>
        <p className="small muted">Save your profile in <a href="#/setup">Setup</a> first - your resume is built from it.</p>
      </div>
    );
  }
  const s = style.style;
  return (
    <div className="card stack">
      <div className="spread">
        <div>
          <b>Your resume</b>
          <div className="muted small">
            {base ? <>Built {day(base.created)}{base.pages ? ` · ${base.pages} page${base.pages > 1 ? "s" : ""}` : ""}</>
              : "Built from your whole profile, in the way you like it. Job resumes start from here."}
          </div>
        </div>
        {base && (
          <span className={`badge ${base.checks_ok ? "strong" : "closed"}`}
                title="Its PDF's text, read back the way an applicant tracking system reads it">
            {base.checks_ok ? "Reads cleanly" : "Read-back check failed"}
          </span>
        )}
      </div>

      {base?.note && <p className="small">{base.note}</p>}
      {base && !base.checks_ok && base.failed.length > 0 && (
        <p className="small fail">Check failed: {base.failed.join(", ")}</p>
      )}
      {base && <Preview key={base.created} />}

      <div className="row">
        <button className={base ? "" : "primary"} disabled={build.running || busy} onClick={rebuild}>
          {build.running ? "Building…" : base ? "Rebuild" : "Build my resume"}
        </button>
        {base?.files.map(f => {
          const ext = (f.name.split(".").pop() || "").toLowerCase();
          return (
            <button key={f.name} className="quiet" onClick={() => openFile(f.url).catch(e => toast((e as Error).message))}>
              <Icon name="file" />{FILE_NAMES[ext] || ext.toUpperCase()}
            </button>
          );
        })}
      </div>
      {build.lines.length > 0 && <div className="console" aria-live="polite">{build.lines.join("\n")}</div>}
      {build.result === "failed" && <p className="fail small">The build didn't finish cleanly - see above.</p>}

      <details open={!base}>
        <summary><b>How you like it</b> <span className="muted small">· starting values follow your years of experience</span></summary>
        <div className="stack" style={{ marginTop: 8 }}>
          <label className="inline">Length
            <select value={s.pages} disabled={busy} style={{ width: "auto", marginLeft: 8 }}
                    onChange={e => change({ pages: e.target.value })}>
              <option value="1">1 page</option>
              <option value="2">2 pages</option>
              <option value="any">No limit</option>
            </select>
          </label>
          {NUMBERS.map(([key, label]) => {
            const [lo, hi] = style.ranges[key as string];
            return (
              <label key={key} className="inline">{label}
                <select value={String(s[key])} disabled={busy} style={{ width: "auto", marginLeft: 8 }}
                        onChange={e => change({ [key]: Number(e.target.value) })}>
                  {Array.from({ length: hi - lo + 1 }, (_, n) => lo + n).map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                {style.chosen.includes(key as string) ? null : <span className="muted small"> (default)</span>}
              </label>
            );
          })}
          <Sections style={style} busy={busy} onSave={sections => change({ sections })} />
          <p className="small muted" style={{ margin: 0 }}>
            When the PDF runs past your length, it is rebuilt with less - extra projects first, then pieces, then
            bullets. Nothing is ever added that isn't in your profile.
          </p>
        </div>
      </details>
    </div>
  );
}

/** Which sections show, and in what order. Skills and Experience always show. */
function Sections({ style, busy, onSave }: { style: StyleView; busy: boolean; onSave: (s: string[]) => void }) {
  const shown = style.style.sections;
  const hidden = style.sections.filter(x => !shown.includes(x));
  const move = (i: number, by: number) => {
    const next = [...shown];
    const j = i + by;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    onSave(next);
  };
  return (
    <div>
      <div className="lbl">Sections, top to bottom</div>
      <ul className="used">
        {shown.map((name, i) => (
          <li key={name} className="row">
            <span className="grow">{SECTION_NAMES[name] || name}</span>
            <button className="quiet small" aria-label={`Move ${name} up`} disabled={busy || i === 0} onClick={() => move(i, -1)}>↑</button>
            <button className="quiet small" aria-label={`Move ${name} down`} disabled={busy || i === shown.length - 1}
                    onClick={() => move(i, 1)}>↓</button>
            {!style.required.includes(name) && (
              <button className="quiet small" disabled={busy} onClick={() => onSave(shown.filter(x => x !== name))}>Hide</button>
            )}
          </li>
        ))}
      </ul>
      {hidden.length > 0 && (
        <div className="row small">
          <span className="muted">Hidden:</span>
          {hidden.map(name => (
            <button key={name} className="quiet small" disabled={busy} onClick={() => onSave([...shown, name])}>
              <Icon name="plus" />{SECTION_NAMES[name] || name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The built page itself, as the reader will see it.
 *
 * Drawn in a shadow root, not an iframe: the app's own security policy
 * (frame-ancestors 'none') carries into a srcdoc frame, which then stays
 * blank. The resume's styles stay inside the shadow root, and DOMParser runs
 * no scripts - the page has none anyway, and every value in it is escaped. */
function Preview() {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ok" | string>("loading");
  useEffect(() => {
    let live = true;
    blob("/files/resumes/base.html").then(b => b.text()).then(html => {
      if (!live || !host.current) return;
      const doc = new DOMParser().parseFromString(html, "text/html");
      const css = Array.from(doc.querySelectorAll("style")).map(s => s.textContent || "").join("\n")
        .replace(/\bbody\s*\{/g, ".page {").replace(/@page[^}]*\}/g, "");
      const root = host.current.shadowRoot || host.current.attachShadow({ mode: "open" });
      root.innerHTML = `<style>${css}</style><div class="page">${doc.body.innerHTML}</div>`;
      setState("ok");
    }).catch(e => live && setState((e as Error).message));
    return () => { live = false; };
  }, []);
  return (
    <>
      {state !== "ok" && <p className="small muted">{state === "loading" ? "Loading the preview…" : `The preview didn't load: ${state}`}</p>}
      <div ref={host} className="resume-preview" aria-label="Your resume" />
    </>
  );
}
