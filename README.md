# system-model

A hierarchical artifact that sits as a **peer to a software system**. Developers (and AI
agents) *read* it to understand current system state at machine speed, and — in later phases —
*write* specifications into it to constrain agents to desired outcomes.

This repo is a standalone product: it targets the Trevorism platform first, but is designed to
apply to other software systems via pluggable adapters.

## Core ideas

- **Code is truth.** The *derived* model is a projection of what the code actually is.
- **Spec is intent** (future). Authored specs are targets, not truth. The gap `derived ≠ authored`
  is the signal — a routed human decision: change the code, or change the spec.
- **One hierarchy:** L0 platform (cross-repo, future) → **L1 service → L2 module → L3 convention →
  L4 invariant**.
- The model is a peer to the code: it is written **into the target repo** at `.systemmodel/`, so it
  lands in the same diff/PR when code changes and drift is visible in review.

## This slice (v0.1): the read path

Derive a repo's L1–L4 **derived** model from its source and write it as a doc tree.

```
python -m systemmodel.derive <repo-name> [--dry-run] [--adapter NAME]
```

`<repo-name>` is a folder under the container dir (this repo's parent, or `$DEV_DIR`) or an
absolute path. Example:

```
python -m systemmodel.derive testing            # writes C:\dev\testing\.systemmodel\
python -m systemmodel.derive testing --dry-run  # preview, writes nothing
```

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

## Roadmap (later phases, out of scope for v0.1)

2. **Authoring + drift** — an authored-spec surface and `derived ≠ authored` gap routing.
3. **Conformance gate** — invariants as machine checks, wired into the test-result pipeline.
4. **L0 cross-repo layer** + a graph/dashboard projection.

## Known limitations (v0.1)

- Extraction is regex/line based; generics with internal spaces (`Map<String, String>`) and
  unconventional layouts may be partially captured.
- Only the `micronaut_groovy` adapter exists.
- Read path only — no authoring, drift, or conformance yet.
