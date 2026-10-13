// Asking for a company to be watched (src/jobsearch/company_requests.py).
// Whoever runs the service looks for its job board from the admin page.

import { useCallback, useEffect, useState } from "react";
import { get, post } from "./api";
import { useToast } from "./ui";

export interface CompanyRequest {
  id: string;
  company: string;
  status: "asked" | "checking" | "watching" | "not_found" | "declined";
  board?: string;
  note?: string;
  postings?: number | null;
  url?: string;
  asked?: number;
  first_asked?: string;
}

export const REQUEST_WORD: Record<string, string> = {
  asked: "waiting to be looked at", checking: "looking for its job board now",
  watching: "watched - its jobs show up from the next update", not_found: "no job board we can read",
  declined: "not added",
};

export function AskCompany() {
  const [mine, setMine] = useState<CompanyRequest[] | null>(null);
  const [company, setCompany] = useState("");
  const [url, setUrl] = useState("");
  const [err, setErr] = useState("");
  const toast = useToast();
  const load = useCallback(() => { get<CompanyRequest[]>("/api/companies").then(setMine).catch(() => setMine([])); }, []);
  useEffect(load, [load]);

  const ask = async () => {
    setErr("");
    if (!company.trim()) { setErr("Name the company"); return; }
    try {
      const r = await post<CompanyRequest>("/api/companies/ask", { company: company.trim(), url: url.trim() });
      toast(r.status === "watching" ? `${r.company} is already watched` : `Asked for ${r.company}`);
      setCompany(""); setUrl("");
      load();
    } catch (e) { setErr((e as Error).message); }
  };
  const withdraw = async (r: CompanyRequest) => {
    try { await post(`/api/companies/${encodeURIComponent(r.id)}/withdraw`); load(); }
    catch (e) { toast((e as Error).message); }
  };

  return (
    <div className="card stack">
      <p style={{ margin: 0 }}><b>Missing a company?</b> <span className="muted small">Ask for it. We look for its job board and,
        if we can read it, its jobs are checked against your profile from then on.</span></p>
      <div className="fields">
        <label>Company<input value={company} maxLength={120} onChange={e => setCompany(e.target.value)} placeholder="e.g. Globex" /></label>
        <label>Its careers page (optional)<input value={url} maxLength={300} onChange={e => setUrl(e.target.value)} placeholder="https://" /></label>
      </div>
      <div className="row">
        <button onClick={ask}>Ask for it</button>
        {err && <span className="fail small">{err}</span>}
      </div>
      {mine && mine.length > 0 && (
        <ul className="asked">
          {mine.map(r => (
            <li key={r.id}>
              <b>{r.company}</b> <span className={`badge ${r.status === "watching" ? "strong" : r.status === "declined" || r.status === "not_found" ? "closed" : ""}`}>
                {REQUEST_WORD[r.status] || r.status}</span>
              {r.note && <span className="muted small"> {r.note}</span>}
              {(r.status === "asked" || r.status === "not_found" || r.status === "declined") && (
                <button className="quiet" onClick={() => withdraw(r)}>Remove</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
