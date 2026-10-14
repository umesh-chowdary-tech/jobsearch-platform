// Market: what the jobs that passed your filters ask for that nothing in
// your profile backs - the personal agent's Market screen.

import { useEffect, useState } from "react";
import { get } from "../api";
import type { Gap } from "../types";

export function Market() {
  const [g, setG] = useState<Gap[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    get<{ gaps: Gap[] }>("/api/market").then(m => setG(m.gaps)).catch(e => setErr((e as Error).message));
  }, []);

  if (err) return <p className="fail">{err}</p>;
  if (!g) return <p className="muted">Loading…</p>;
  const top = Math.max(...g.map(x => x.required + x.nice), 1);
  return (
    <>
      <h1>Market</h1>
      <p className="sub">What kept postings ask for that nothing in your profile backs. What the market asks for most,
        which isn't the same as what makes you worth hiring.</p>
      <div className="card">
        {g.length ? (
          <>
            <div className="legend" style={{ marginTop: 0 }}><span><i className="req" />Required</span><span><i className="nice" />Nice to have</span></div>
            <div className="gaps">
              {g.map(x => (
                <div key={x.skill} className="gap">
                  <div>
                    <b>{x.skill}</b>
                    <div className="muted small">{x.bucket === "shallow" ? "in your profile, but only lightly" : "not in your profile"}</div>
                  </div>
                  <div className="bars" role="img" aria-label={`${x.required} jobs require it, ${x.nice} call it nice to have`}>
                    <i className="req" style={{ width: `${(100 * x.required) / top}%` }} />
                    <i className="nice" style={{ width: `${(100 * x.nice) / top}%` }} />
                  </div>
                  <span className="small num">{x.required} required{x.nice ? ` · ${x.nice} nice` : ""}</span>
                </div>
              ))}
            </div>
            <p className="small muted" style={{ marginBottom: 0 }}>If you have really used one, <a href="#/setup">add where in Setup</a> and
              it counts from then on.</p>
          </>
        ) : <p className="empty" style={{ margin: 0 }}>No gaps recorded yet: they appear once jobs have been checked against your profile.</p>}
      </div>
    </>
  );
}
