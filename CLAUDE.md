# CLAUDE.md

## What this project is
A Telegram bot that posts a daily news digest (07:00 IST) to a group of friends, showing how left,
right and international outlets frame the same story, plus conservative breaking-news alerts.
It is also a public portfolio project: code, decisions and history should be easy for others to read.

## Stack
- Python 3.12, managed with uv. Runs on GitHub Actions (no server in v1).
- Sources: curated RSS feeds (`config/sources.yaml`) + GDELT for breaking-news spikes.
- Clustering: sentence-transformers multilingual embeddings, run locally.
- AI: Claude Haiku 5.5 for translation, lean judging and breaking summaries; Claude Sonnet 5.5 only
  for the daily framing write-up. The bot calls the Anthropic API directly, not Claude Code.
- Delivery: python-telegram-bot, send-only, group with Topics, URL buttons.
- State: SQLite at `data/state.db`, committed back by the workflow.
- Design overview, selection rules and build order: `docs/architecture.md`. Reasoning: `docs/adr/`.
  Keep `docs/architecture.md` up to date when a design decision changes.

## Commands
- Install: `uv sync`
- Test: `uv run pytest -q`
- Lint and format: `uv run ruff check . --fix` and `uv run ruff format .`
- Type check: `uv run pyright`
- Preview the digest without posting: `uv run python -m news_summariser digest --dry-run`
- Commit hooks (ruff, pyright, pytest) are set up in `.pre-commit-config.yaml`.

## Definition of done
A task is done only when tests pass, ruff and pyright are clean, and, for any change to the
pipeline, `--dry-run` produces a digest. If anything fails, stop: show the error output, explain the
likely cause in plain words, suggest a fix, and wait for me. Don't try fixes on your own first.

## Code style
- ruff for formatting and linting; pyright in basic mode.
- Type hints on every function. Data passed between modules uses pydantic models.
- One-line docstrings on public functions only. Comments explain *why*, not *what*
  (e.g. why a threshold has a given value).

## Ownership: I write these, Claude never edits them
The core logic is mine. This covers the modules that do it, whatever they're named:
- Story clustering: grouping articles into stories (currently `src/news_summariser/cluster.py`)
- Story selection: ranking, topic rotation, blindspot rules (currently `src/news_summariser/select.py`)
- The lean-judging rubric (currently `prompts/judge.md`)

For bugs or improvements in these, show me a diff in chat with the reason. I apply it myself.
If you rename or move one of these files, update this section in the same change.

## Needs my approval before any change
- `config/sources.yaml` (outlets and baseline leans) and everything in `prompts/`.
  Never rename a source `id`; history and dedupe are keyed on it. Change only its `url`.
- Adding any Python package, including dev tools: say why it's needed and what the alternatives are.
- AI models, the spend cap, schedules, and anything in `.github/workflows/`.

## Workflow
- Claude never runs `git commit` or `git push`. I review and make every commit.
- If you suggest a commit message, don't add a Co-Authored-By trailer.
- Never work on `main`. One branch per feature: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
- Run `/code-review` before I open a PR. I review and merge every PR.
- Write an ADR in `docs/adr/` (numbered `NNNN-title.md`) before changing a dependency, a data format
  or the database schema, an AI model, hosting, or the lean rubric. Not for routine features or fixes.
  Use `/adr` to create one.

## Claude Code tooling
- Hooks (`.claude/settings.json`): `git commit`/`git push` are blocked; `uv add` and `pip install`
  ask me first; every Python file Claude edits is run through ruff.
- Before committing changes to `prompts/` or `config/sources.yaml`, run the `bias-auditor` agent.
- When feeds look quiet or before adding sources, run the `source-curator` agent.
- Both agents are read-only; their findings come to me to decide.

## How Claude works with me
- I'm the lead engineer. Before every design choice, give me 2–3 options with pros and cons, a
  recommendation and the reason, then ask me and wait.
- Ask me questions as we go so I know what's happening; I answer, you do the writing.
- After each module, ask me one short "why would X break here?" quiz question.
- Never guess prices, model IDs or API behaviour. Check the official docs.

## Secrets and safety
- Secrets live only in `.env` (local, gitignored) and GitHub Secrets. `.env.example` lists the names.
- Never print, log or commit API keys, bot tokens or chat IDs. This repo is public.
- Respect paywalls and robots.txt. If full text isn't freely available, use the RSS summary.

## Budget
- AI spend cap: $4/month, tracked in `budget.py`. When it's hit, send a headline-only digest and
  log a warning. Never raise the cap without asking me.
