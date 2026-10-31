# 0006. Clustering: multilingual embeddings, agglomerative clustering and the Story model

- **Status:** Accepted
- **Date:** 2026-10-31

## Context
Ingest keeps about 1–3k articles from the last 48 hours, from 38 outlets in 9 languages. To compare
framing, articles about the same event have to be grouped, across languages, before any paid
translation. Clustering runs on CPU-only GitHub Actions runners, every 20 minutes for breaking news
and daily for the digest. It must cost $0 and keep outlets' text and anything derived from it out of
the public repo. The clustering logic itself (`cluster.py`) is owner-written; this ADR fixes the
tools, storage and interface around it.

## Options considered
### Embedding stack
#### sentence-transformers + `paraphrase-multilingual-MiniLM-L12-v2` (chosen)
- ✅ The best-known library for sentence embeddings.
- ✅ The model is trained so sentences with the same meaning in different languages land close
  together: 50 languages, 384-dimension vectors, Apache-2.0 licence.
- ❌ Pulls in PyTorch, a large install, unless pinned to the CPU-only build and cached.
- ❌ Reads at most 128 tokens per text; anything longer is cut off.

#### fastembed (ONNX runtime)
- ✅ Much lighter: no PyTorch, faster installs on Actions.
- ❌ Fewer models and less well known.

#### An embedding API
- ✅ Nothing to install.
- ❌ Costs money or depends on rate limits on every run, against the budget and ADR 0001.

### Clustering method
#### scikit-learn `AgglomerativeClustering`, cosine distance, average linkage, `distance_threshold` (chosen)
- ✅ The textbook method: keep merging the closest groups until they are further apart than the
  threshold. Deterministic, and needs no cluster count up front.
- ✅ The threshold is one number that can be explained, tested and justified in a comment.
- ❌ Adds scikit-learn, and compares every pair of articles.

#### `community_detection` from sentence-transformers
- ✅ No extra package.
- ❌ Greedy around dense "centres", so a loosely covered story can split in two.

#### HDBSCAN
- ✅ Finds group density by itself and leaves one-off articles out as noise.
- ❌ Harder to reason about and tune on small, uneven daily data; results are harder to defend.

### Where embeddings live
#### `cache.db`, keyed by `url_hash`, with a `model` column (chosen)
- ✅ Each run embeds only new articles; the migration setup from ADR 0005 already handles it.
- ✅ The `model` column marks vectors from an old model as stale, so they're redone, never mixed.
- ❌ A schema change, and the rows must be pruned with the articles.

#### Recompute everything each run
- ✅ No schema change, never stale.
- ❌ Re-embeds the whole 48-hour window every 20 minutes.

#### A separate `.npy` file
- ❌ A second file to keep in step with the database, with no migrations.

### Text fed to the model
#### Title + summary, cut at 128 tokens by the model (chosen)
- ✅ The most context, which helps vague headlines ("Live updates"). The title comes first, so it is
  always included in full.
- ❌ Uneven summaries add a little noise to similarity scores.

#### Title only
- ✅ Consistent and never cut off.
- ❌ Vague headlines give the model almost nothing, so unrelated stories can merge.

#### Title + first sentence of the summary
- ✅ Cleaner input.
- ❌ Sentence splitting across Japanese, Hindi and Arabic punctuation is fiddly and could fail
  quietly for one language.

### Story model
#### `story_id` + full `Article` list + derived properties (chosen)
- ✅ Holds what select, judge and render need; derived properties (`source_ids`,
  `first_published`, `last_published`) can't contradict the articles.
- ✅ Region, lean and ownership stay only in `sources.yaml`.
- ❌ `select.py` takes the sources config as a second input.

#### `story_id` + `url_hash` list only
- ❌ Every consumer looks the articles up again.

#### Articles plus copied region, lean and ownership
- ❌ The same facts in two places; a changed baseline would leave stale copies.

## Decision
- **Embeddings:** sentence-transformers with `paraphrase-multilingual-MiniLM-L12-v2`.
- **PyTorch is pinned to the CPU-only build** through uv's `pytorch-cpu` index (`explicit = true`).
  Standard GitHub-hosted runners for public repos (4 CPU, 16 GB RAM) have no GPU. GPU runners are
  "larger runners": only for GitHub Team or Enterprise Cloud organisations, and billed per minute
  even for public repos, so they are out of reach and out of budget. On Linux, PyPI's default
  PyTorch is the CUDA build, which would download GPU libraries that are never used. A GPU isn't
  needed anyway: the model is small (~0.1B parameters) and, with stored embeddings, each run embeds
  only a few dozen new articles. On Windows PyPI already serves the CPU build; the pin makes local
  and Actions installs match.
- **Clustering:** scikit-learn `AgglomerativeClustering` with cosine distance, average linkage and a
  distance threshold, written by the project owner in `cluster.py`.
- **Storage:** embeddings live in `cache.db` (migration `0002`), keyed by `url_hash`, with a `model`
  column, and are pruned with the articles at 48 hours.
- **Input text:** title + summary, joined, with the model cutting at 128 tokens.
- **Output:** `Story` holds a `story_id` (the `url_hash` of its earliest article), the full
  `Article` list, and derived properties.

## Consequences
- Two new runtime packages: sentence-transformers (with PyTorch, CPU-only) and scikit-learn.
  `torch` is also listed directly in `pyproject.toml`, as in uv's own CPU-only example, because
  uv's docs don't say whether `[tool.uv.sources]` applies to packages we don't list ourselves.
  The lockfile resolves Linux and Windows to `torch 2.15.0+cpu`, with no NVIDIA packages.
- The model weights are about 471 MB. Actions must cache them, or every run downloads them again.
- `cache.db` gets migration `0002`.
- Clustering compares every pair of articles: about 9M distances at 3k articles. That fits in memory
  now, but grows with the square of the article count.
- Story ids can drift: when a story's earliest article ages out of the 48-hour window, the story
  gets a new id, so a long-running story could look new again. `select.py` must handle this, for
  example by checking overlap with stories already posted.
- The distance threshold is a tuned number owned by the project owner. It needs a small
  hand-labelled set of articles to tune and test against.
- `select.py` takes the sources config as a second input, to look up region and lean.

## Revisit if
- Embedding plus clustering takes more than 60 s per run on Actions.
- Install plus model load takes more than 2 min per run, even with caching.
- Spot checks find more than about 1 in 5 digest stories merging unrelated events or splitting one
  event in two.
- The same event in two languages lands in separate stories in more than about 1 in 5 checks.
