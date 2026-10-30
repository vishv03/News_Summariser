---
name: adr
description: Create a new Architecture Decision Record in docs/adr/ by asking the user questions, then writing it from their answers. Use when the user runs /adr, or before a hard-to-reverse change listed in CLAUDE.md.
---

# /adr — new Architecture Decision Record

The user is the decision-maker. You ask; they decide; you write.

## Steps
1. **Number:** find the highest `NNNN-*.md` in `docs/adr/` and use the next number (4 digits).
2. **Context:** if the user gave a title as the argument, use it. Summarise in 2–3 sentences what
   problem this decision solves and which constraints matter (budget $0–5/month, public repo,
   GitHub Actions, the user's ownership rules). Ask the user to confirm or correct it.
3. **Options:** propose 2–3 realistic options with pros and cons each, and say which you recommend
   and why. Ask the user to pick, using the AskUserQuestion tool with your recommendation first.
4. **Consequences and revisit signals:** ask what they expect to become harder, and suggest 2–3
   concrete "revisit if" signals (numbers where possible). Let them adjust.
5. **Write** `docs/adr/NNNN-kebab-title.md` from `docs/adr/template.md`, status Accepted, today's date.
6. **Index:** add a row to the table in `docs/adr/README.md`.
7. **Hand over:** list the files changed and suggest a commit message
   (`docs: add ADR NNNN <title>`, no Co-Authored-By trailer). Never commit yourself.

If this decision replaces an earlier ADR, set the old one's status to "Superseded by NNNN".
