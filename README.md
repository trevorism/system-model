# system-model

A hierarchical artifact that sits as a **peer to a software system**. Developers (and AI
agents) *read* it to understand current system state at machine speed, and — in later phases —
*write* specifications into it to constrain agents to desired outcomes.

This repo is a standalone product: it targets the Trevorism platform first, but is designed to
apply to other software systems via pluggable adapters.

## Where the model lives

The model is written to a **standalone directory**, not into each target repo. By default that's
`C:/systemmodel` (a sibling of the container dir); override it with the `SYSTEMMODEL_DIR`
environment variable. The **root** holds the L0 platform model plus the authored `platform.toml`;
each repo's model lives in a **flat subdirectory named after the repo**:

```
$SYSTEMMODEL_DIR/            # e.g. C:/systemmodel
  platform.toml             # authored policy (see below)
  platform.md               # L0 platform model
  capabilities.md           # L0 cross-repo capability map
  invariants.md
  conventions.md
  MANIFEST.json             # platform manifest
  <repo>/                   # one subdir per repo
    capabilities.md         # end-user view (lead read) — see below
    service.md
    modules/…
    conventions.md
    invariants.md
    MANIFEST.json
```

Target repos stay pristine — nothing is written into them. The model is *derived* (code is truth),
so there's no data to migrate: point the tool at a machine and re-derive.

## Usage

Prerequisite: [uv](https://docs.astral.sh/uv/). No install or venv activation needed — `uv run`
builds and runs it (the first run creates `.venv`; after that it's instant).

```
# one repo
uv run systemmodel <repo>              # derive $SYSTEMMODEL_DIR/<repo>/  (code -> model)
uv run systemmodel <repo> --dry-run    # preview, write nothing
uv run systemmodel <repo> --check      # is the model stale vs the code? exit 1 if so
uv run systemmodel <repo> --apply      # spec -> code: emit a change brief from the edited model
uv run systemmodel <repo> --auto       # spec -> code: drive an agent from the brief, then verify
uv run systemmodel <repo> --gate       # conformance: exit 1 if code violates its edited spec

# whole platform
uv run systemmodel --all               # derive every auto-detected repo in the container
uv run systemmodel --all --check       # staleness check across all repos (for CI)
uv run systemmodel --all --gate        # conformance check across all repos (for CI)
uv run systemmodel --platform          # L0 platform model -> $SYSTEMMODEL_DIR/ (root)
uv run systemmodel --platform --check
uv run systemmodel --platform --gate   # exit 1 if platform.toml requirements are violated
```

`<repo>` is a folder name under the container dir (this repo's parent, or `$DEV_DIR`) or an
absolute path — e.g. `uv run systemmodel testing`. The model output dir is `$SYSTEMMODEL_DIR`
(default `C:/systemmodel`).

Keep models honest by wiring `--check` into CI or a pre-commit hook — it fails the build when a
repo's model has drifted from its code:

```
uv run systemmodel "$(basename "$PWD")" --check
```

`--check` and `--gate` are two **different axes** — run both in CI: `--check` asks *is the committed
model stale vs the code?* (drift); `--gate` asks *does the code satisfy authored intent?*
(conformance). A model can be perfectly fresh yet still violate an authored requirement, so a green
`--check` doesn't imply a green `--gate`. Both write nothing and exit 1 on failure. `--gate` reads
authored intent from `platform.toml` requirements (`--platform`) and from a repo's hand-edited spec
(repo / `--all`); with no authored intent it's a no-op pass.

(Equivalent without uv: `python -m systemmodel.derive <repo> ...`.)

## Core ideas

- **Code is truth.** The *derived* model is a projection of what the code actually is.
- **Spec is intent** (future). Authored specs are targets, not truth. The gap `derived ≠ authored`
  is the signal — a routed human decision: change the code, or change the spec.
- **One hierarchy:** **L0 platform (cross-repo)** → **L1 service / capabilities → L2 module →
  L3 convention → L4 invariant**.
- **Lead with what it does for people.** The low-level facts (versions, endpoints, env) are for
  machines and are easy to read off the code directly; a version bump from `5.0.0` → `5.0.2` is not
  a meaningful *suggestion*. The model instead leads with **capabilities** — end-user / user-story
  statements synthesized from those facts — and demotes the technical detail to a footnote.
- The model is a peer to the code, kept in a **standalone directory** (`$SYSTEMMODEL_DIR`, default
  `C:/systemmodel`) rather than embedded in each repo — one place to read the whole platform, and
  target repos stay pristine.

## Capabilities: the end-user altitude

`capabilities.md` is the **lead read** for a repo. It answers *what can a person or another service
do here?* rather than *how is it built?* Each capability is a user story composed deterministically
from facts the adapter already extracts:

> **As {actor}, I can {action} {object}{outcome}.**

- *actor* ← the security matrix (`@Secure` role → "an authenticated app"; no `@Secure` → "anyone (public)").
- *action* ← the HTTP verb (GET → view/list, POST → create/submit, PUT → update, DELETE → remove).
- *object* ← the resource the route acts on.
- *outcome* ← the collaborators of the services a controller injects (a `Repository` → "and it is
  stored"; an event-channel client → "and published as an event").

The doc leads with an **Exposure** callout — *public write* capabilities (mutating endpoints with no
`@Secure`) — which is the headline "verify this" signal, replacing the version-bump nag. Because it's
derived from code, `capabilities.md` participates in `--check` / `--apply` / `--gate` like every other
doc: edit a story (e.g. change an actor from public to authenticated) and `--apply` emits a change
brief pointing at the exact controllers/services to edit.

### Authored intent overlay

Deterministic stories stay honest and diff-stable, but they read mechanically. So each capability
carries an **authored overlay** — an invisible anchored region a human or agent fills with narrative
intent that re-derivation *preserves*:

```
#### As an authenticated app, I can submit a test result and it is stored. <!-- cap:event.testResult.submit -->
↳ `POST /event/testResult` · `submit`

<!-- intent:event.testResult.submit -->
> intent: the platform's test-history spine; every suite run lands here for fan-out.
<!-- /intent -->
```

The intent prose is **not** code-reconcilable, so it's excluded from the content hash (a prose edit
is never reported as drift by `--check`) and stripped from `--apply` diffs (it never appears as a
phantom code gap). A `derive` recovers the prose from the prior file and re-injects it; if a
capability disappears, its orphaned intent is dropped and reported. The `L0 capabilities.md` rolls
these up across services into a cross-repo capability map plus a platform-wide exposure list.

## Two altitudes: platform vs repo

Some facts belong to the **platform**, not any one repo (security enabled, HTTPS, JDK pin, coverage
gate). Those live once in the **L0 platform model** at the standalone root (`$SYSTEMMODEL_DIR/`) —
the platform's `~/.claude` to a repo's project `.claude`. It is *derived by aggregation*: `--platform` reads every
repo's code and, per signal, records how many repos satisfy it and **which ones don't** (outliers =
drift from the norm). Each repo's `invariants.md` then shows its own values under **Platform-governed**
(pointing here) and keeps only genuinely local facts under **Repo-specific**.

### Repo classification

Platform invariants only make sense for deployable **services**, so every repo is classified first —
`service` / `library` / `tester` / `template` / `experiment` — derived from structural signals
(App-Engine descriptor + Micronaut application plugin = service; publishes a jar/plugin = library;
`*-tester` / `template-*` by name; source with none of these = experiment). The `--platform` model
aggregates invariants over **services only** and publishes a **repo census**, so libraries and
experiments no longer show up as false outliers. `platform.toml` (optional, at the standalone
model root) overrides a repo's kind when intent differs from structure, or widens which kinds get
aggregated.

### Authored intent (descriptive vs prescriptive)

By default the platform model is **descriptive** — it reports the *observed norm* (what most services
do) and names statistical outliers. Author intent in `platform.toml` to make a signal
**prescriptive** — a *requirement* that services are measured against, where any non-matching repo is
a **violation** (the `derived ≠ authored` gap): fix the code, or change the spec.

```toml
[invariants]          # bool signals every service MUST satisfy
security.enabled    = true
https.secure_always = true
coverage.gate       = true

[conventions]         # value signals with a required value (quote values: "25", not 25)
jdk               = "25"
micronaut.version = "5.0.2"
```

The L0 `platform.md` then leads with a **Conformance** summary (requirements, signals with
violations, repos in violation); `invariants.md` / `conventions.md` mark each authored signal
**REQUIRED** with a conform count and violators, while un-authored signals stay labeled as the
observed norm. (This is platform-level authoring; per-repo authored specs are a later slice.)

## Two directions: derive vs apply

The model reconciles code and intent, and **you pick which side wins by the command you run**:

- **`derive` — code is truth.** Re-reads the code and (re)writes the repo's model under
  `$SYSTEMMODEL_DIR/<repo>/`. Use it after the code changes.
- **`--apply` — spec is truth.** You edit the model's `*.md` to describe *desired* state; this
  re-derives the current code **in memory** (never overwriting your edits), diffs it against your
  edited spec, and emits a **change brief** (`$SYSTEMMODEL_DIR/<repo>/change-brief.md` + stdout):
  per changed document, the `current → desired` diff and the **source files to edit** (from each
  node's `derived_from`), with the acceptance criterion `uv run systemmodel <repo> --check` is clean.

system-model does **not** edit code — the brief is handed to an agent (Claude Code) or the developer,
who makes the change; re-derivation is the acceptance test that the change conformed.

`--auto` closes this loop: it feeds the brief to the `claude` CLI (non-interactively) so the agent
edits the repo, then re-derives and rebuilds the brief as the acceptance gate, looping on any
residual drift up to `--max-iters` (default 3). system-model still never edits code — it only
orchestrates the agent and verifies. Guardrails: it refuses on a dirty git working tree (so the
agent's edits stay isolated and revertable) and never commits; the agent runs with `acceptEdits`
by default, or `--dangerous` (`--dangerously-skip-permissions`) if it needs Bash/tests. Preview
with `--dry-run` (prints the brief + the command, mutates nothing).

Note: the `.md` files double as the spec format for now, so a hand-edit must be internally consistent
— e.g. securing an endpoint means updating both the route table *and* the "unsecured endpoints"
section it feeds. A more formal spec format (tighter targets, machine-checkable acceptance) is on the
roadmap.

## How it stays honest

The model is only truth if it's regenerated when the code changes. `--check` re-derives in memory,
compares content hashes to the model's `MANIFEST.json`, prints what drifted
(`added`/`changed`/`removed`/`stale file`), and exits non-zero if anything is stale — so it drops
straight into a pre-commit hook or CI step. `--all` turns platform-wide rollout/refresh into one
command: it walks the container, runs each adapter's `detect()`, and derives (or `--check`s) every
repo an adapter matches, skipping the rest.

Pruning is manifest-driven: a re-run removes only the files a prior run recorded in `MANIFEST.json`
and no longer produces — so the per-repo subdirs and `platform.toml` that share the standalone root
are never touched by another target's run.

## Output layout (`$SYSTEMMODEL_DIR/<repo>/`)

```
capabilities.md       # L1  end-user view (lead read): user stories + exposure + authored intent
service.md            # L1  service identity, host, liveness + demoted technical footer (version)
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
new adapter, never a fork. An adapter implements `detect()` plus `extract_service/capabilities/
modules/conventions/invariants`, returning pre-rendered Nodes; the core owns the frontmatter/manifest
envelope. A node can set `supports_authored=True` to opt into the authored overlay (`core/overlay.py`),
which the render/apply core preserves and excludes from hashing/diffing.

## Roadmap (later phases)

2. **Authoring + drift** — *platform-level done* (authored `[invariants]`/`[conventions]`); *spec→code
   done* (`--apply` emits a change brief, `--auto` drives an agent from it and re-derives to verify).
   Next: a formal per-repo spec format.
3. **Conformance gate** — *exit code done* (`--gate` fails on authored platform.toml violations and
   per-repo apply gaps). Next: wire it into the test-result pipeline (needs a conformance suite
   `kind` across the event + testing services).
4. **Graph/dashboard projection** over the model.

## Known limitations

- Extraction is regex/line based; generics with internal spaces (`Map<String, String>`) and
  unconventional layouts may be partially captured.
- Only the `micronaut_groovy` adapter exists, so repos without Micronaut/Groovy markers (e.g. some
  pure-Java shared libraries) aren't detected/classified at all — they'd need their own adapter.
- The conformance gate (`--gate`) produces a failing exit code, but isn't yet wired into the
  test-result pipeline as a suite result (roadmap item 3).
