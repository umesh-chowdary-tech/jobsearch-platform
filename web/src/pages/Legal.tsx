// The privacy notice and the terms, read from web/src/legal/*.md so the text
// lives in one place. A small Markdown reader turns it into elements - no
// HTML is ever inserted - covering what those two files use: headings,
// paragraphs, bullet lists, tables, **bold** and [links](#/page).

import type { ReactNode } from "react";
import { operator, type Operator } from "../auth";
import privacy from "../legal/privacy.md?raw";
import terms from "../legal/terms.md?raw";

export const LEGAL_VERSION = "2026-10-12-draft";
const DOCS: Record<string, string> = { privacy, terms };

/** The blanks in the text - who runs the service, where, and how to reach
 * them - filled from its settings. One that isn't set stays in [brackets]. */
export function fill(md: string, op?: Operator): string {
  return md
    .replaceAll("[Operator name]", op?.name || "[Operator name]")
    .replaceAll("[city]", op?.city || "[city]")
    .replaceAll("[contact email]", op?.email ? `[${op.email}](mailto:${op.email})` : "[contact email]");
}

function safeHref(href: string): string | null {
  return /^(https:\/\/|mailto:|#\/)/.test(href) ? href : null;
}

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<b key={m.index}>{m[1]}</b>);
    else {
      const href = safeHref(m[3]);
      out.push(href ? <a key={m.index} href={href}>{m[2]}</a> : m[2]);
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (row: string) => row.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());

export function renderMarkdown(md: string): ReactNode[] {
  const lines = md.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      const body = inline(h[2]);
      out.push(level === 1 ? <h1 key={i}>{body}</h1> : level === 2 ? <h2 key={i}>{body}</h2> : <h3 key={i}>{body}</h3>);
      i++;
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const [head, ...rest] = rows;
      const body = rest.filter(r => !/^\|[\s:|-]+\|$/.test(r.trim()));
      out.push(
        <div key={i} className="card scroll-x"><table>
          <thead><tr>{cells(head).map((c, n) => <th key={n}>{inline(c)}</th>)}</tr></thead>
          <tbody>{body.map((r, n) => <tr key={n}>{cells(r).map((c, k) => <td key={k}>{inline(c)}</td>)}</tr>)}</tbody>
        </table></div>,
      );
      continue;
    }
    if (/^- /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^- /.test(lines[i])) items.push(lines[i++].slice(2));
      out.push(<ul key={i}>{items.map((t, n) => <li key={n}>{inline(t)}</li>)}</ul>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#|\||- )/.test(lines[i])) para.push(lines[i++]);
    out.push(<p key={i}>{inline(para.join(" "))}</p>);
  }
  return out;
}

export function Legal({ doc, standalone }: { doc: string; standalone?: boolean }) {
  const md = DOCS[doc];
  if (!md) return null;
  const body = <article className="legal">{renderMarkdown(fill(md, operator()))}</article>;
  if (!standalone) return body;
  return (
    <div className="legal-page">
      <p><a href="#/">← Back</a></p>
      {body}
    </div>
  );
}
