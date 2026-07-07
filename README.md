# system-model

A hierarchical artifact that sits as a **peer to a software system**. Developers (and AI
agents) *read* it to understand current system state at machine speed, and — in later phases —
*write* specifications into it to constrain agents to desired outcomes.

This repo is a standalone product: it targets the Trevorism platform first, but is designed to
apply to other software systems via pluggable adapters.

## Usage

Prerequisite: [uv](https://docs.astral.sh/uv/). No install or venv activation needed — `uv run`
builds and runs it (the first run creates `.venv`; after that it's instant).

```
# one repo
uv run systemmodel <repo>              # derive <repo>/.systemmodel/
uv run systemmodel <repo> --dry-run    # preview, write nothing
uv run systemmodel <repo> --check      # is the model stale vs the code? exit 1 if so

# whole platform
uv run systemmodel --all               # derive every auto-detected repo in the container
uv run systemmodel --all --check       # staleness check across all repos (for CI)
uv run systemmodel --platform          # L0 platform model -> system-model/.systemmodel/
uv run systemmodel --platform --check
```

`<repo>` is a folder name under the container dir (this repo's parent, or `$DEV_DIR`) or an
absolute path — e.g. `uv run systemmodel testing`.

Keep models honest by wiring `--check` into CI or a pre-commit hook — it fails the build when a
repo's `.systemmodel/` has drifted from its code:

```
uv run systemmodel "$(basename "$PWD")" --check
```

(Equivalent without uv: `python -m systemmodel.derive <repo> ...`.)

## Core ideas

- **Code is truth.** The *derived* model is a projection of what the code actually is.
- **Spec is intent** (future). Authored specs are targets, not truth. The gap `derived ≠ authored`
  is the signal — a routed human decision: change the code, or change the spec.
- **One hierarchy:** **L0 platform (cross-repo)** → **L1 service → L2 module → L3 convention →
  L4 invariant**.
- The model is a peer to the code: it is written **into the target repo** at `.systemmodel/`, so it
  lands in the same diff/PR when code changes and drift is visible in review.

## Two altitudes: platform vs repo

Some facts belong to the **platform**, not any one repo (security enabled, HTTPS, JDK pin, coverage
gate). Those live once in the **L0 platform model** at `system-model/.systemmodel/` — the platform's
`~/.claude` to a repo's project `.claude`. It is *derived by aggregation*: `--platform` reads every
repo's code and, per signal, records how many repos satisfy it and **which ones don't** (outliers =
drift from the norm). Each repo's `invariants.md` then shows its own values under **Platform-governed**
(pointing here) and keeps only genuinely local facts under **Repo-specific**.

### Repo classification

Platform invariants only make sense for deployable **services**, so every repo is classified first —
`service` / `library` / `tester` / `template` / `experiment` — derived from structural signals
(App-Engine descriptor + Micronaut application plugin = service; publishes a jar/plugin = library;
`*-tester` / `template-*` by name; source with none of these = experiment). The `--platform` model
aggregates invariants over **services only** and publishes a **repo census**, so libraries and
experiments no longer show up as false outliers. `platform.toml` (optional, in the repo root)
overrides a repo's kind when intent differs from structure, or widens which kinds get aggregated.

## How it stays honest

The model lives in the target repo and is only truth if it's regenerated when the code changes.
`--check` re-derives in memory, compares content hashes to the checked-in `MANIFEST.json`, prints
what drifted (`added`/`changed`/`removed`/`stale file`), and exits non-zero if anything is stale —
so it drops straight into a pre-commit hook or CI step. `--all` turns platform-wide rollout/refresh
into one command: it walks the container, runs each adapter's `detect()`, and derives (or `--check`s)
every repo an adapter matches, skipping the rest.

## Output layout (`<repo>/.systemmodel/`)

```
service.md            # L1  service identity, version (+ drift), host, liveness
modules/
  controllers.md      # L2  route table + security matrix + DI graph
  services.md         # L2  service registry (interface + Default<Name>), collaborators
  domain.md           # L2  domain types / enums + fields
conventions.md        # L3  build/test/naming conventions
invariants.md         # L4  coverage gate, security, transport, unsecured endpoints
MANIFEST.json         # machine index: nodes, provenance (derived_from), content hashes
```

Each doc has YAML frontmatter (`level`, `kind`, `id`, `adapter`, `status`, `derived_from`,
`generator_version`, `generated_at`). `content_hash` in `MANIFEST.json` hashes body only, so
re-runs are diff-stable and later phases get a cheap change-stream.

## Architecture

```
systemmodel/
  core/           # system-agnostic: schema, render, adapter interface+registry, locate, filters
  adapters/
    micronaut_groovy/   # first adapter: Micronaut + Groovy + Gradle + GCP App Engine
  derive.py       # CLI: select adapter -> extract -> render
```

The `Adapter` interface (`core/adapter.py`) is the extensibility seam: a new target system is a
new adapter, never a fork. An adapter implements `detect()` plus `extract_service/modules/
conventions/invariants`, returning pre-rendered Nodes; the core owns the frontmatter/manifest
envelope.

## Roadmap (later phases)

2. **Authoring + drift** — an authored-spec surface and `derived ≠ authored` gap routing.
3. **Conformance gate** — invariants as machine checks, wired into the test-result pipeline.
4. **Graph/dashboard projection** over the model.

## Known limitations

- Extraction is regex/line based; generics with internal spaces (`Map<String, String>`) and
  unconventional layouts may be partially captured.
- Only the `micronaut_groovy` adapter exists, so repos without Micronaut/Groovy markers (e.g. some
  pure-Java shared libraries) aren't detected/classified at all — they'd need their own adapter.
- Read path only — no authoring, drift, or conformance yet.
