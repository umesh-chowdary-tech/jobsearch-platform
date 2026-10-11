# Security

This project will hold people's resumes, job searches and application history,
and the keys that pay for its AI calls. Reports that help keep those safe are
very welcome.

## Reporting a vulnerability

**Please don't open a public issue.** Use GitHub's private reporting instead:
the repository's **Security** tab → **Report a vulnerability**. Only the
maintainers see it.

Include what you found, how to reproduce it, and what an attacker could do with
it. You'll get an acknowledgement within a few days and a note when it's fixed.
Please give us a reasonable time to fix it before talking about it publicly.

## What counts

- A way to read or change another person's profile, results, resumes or
  applications
- A key, token, password or piece of personal data in the repository or its
  history
- A way around the AI caps (spending beyond `data/llm-limits.yaml`)
- The local app (`jobsearch serve`) serving a file outside its resume folders,
  accepting a cross-site request, or exposing a key to the browser
- The connector answering on a path other than its secret one
- The inbox reader changing or sending mail (it must be read-only)
- A job board being polled against its robots.txt or rate limits

## What the project does about it

- **No secrets or personal data in the repository.** `.env` is gitignored;
  `.env.example` holds empty values only. Real profiles are gitignored and
  belong outside the checkout; only fictional `sample-*` profiles are committed.
- **Secret scanning on every push and pull request** (gitleaks, over the whole
  history), plus GitHub's secret scanning with push protection.
- **Dependabot** keeps dependencies and workflow actions current.
- **The local app** binds 127.0.0.1, checks the Host header and the Origin,
  serves files only from the resume folders by resolved path, and never sends
  keys to the browser. Each protection has a test (docs/app.md, "Security").

## If you committed a secret

Revoke it at the provider first - removing it from git does not make it safe,
because forks and clones keep history. Then tell the maintainers through the
private report above.
