# system-model

A model of a software system, kept as a peer to the code. People and agents **read** it to learn
what each system promises and what depends on it, and **write** obligations into it that the code
is then held to.

It deliberately does not describe structure. Route tables, dependency graphs within a repo and
type surfaces are all one `rg` away; the model publishes only what reading the code doesn't cheaply
give you — intent, cross-repo blast radius, and which promises the code no longer keeps.

Targets the Trevorism platform first, but the stack knowledge lives in pluggable adapters.

## What it produces

The model is written to a standalone directory (`$SYSTEMMODEL_DIR`, default `C:/systemmodel`),
never into the target repos.

```
$SYSTEMMODEL_DIR/
  platform.toml           # authored policy — yours, never generated
  platform.md             # service graph, repo census, conformance, unauthenticated writes
  graph.md                # every service-to-service edge, both directions
  invariants.md           # authored platform requirements vs the observed norm
  MANIFEST.json
  <repo>/
    overview.md           # purpose, headline requirements, wiring, feature index
    features/<slug>.md    # one capability each, with its own requirements
    intent.md             # yours — never generated, never pruned
    MANIFEST.json
```

Generated documents are plain Markdown with no marker syntax: nothing a reader has to be told to
ignore. Machine state — content, evidence and anchor hashes — lives in `MANIFEST.json`.

## Requirements

A requirement is one obligation, in one place, anchored to the code it rests on.

```markdown
### R1
Access tokens live fifteen minutes and carry role, database id and entity type.
→ `AccessTokenService.createClaimsMap`, `Identity.getPermissions`

### R2 — authored, verified
Role is derived, never requested: apps get system; users get user, escalating only
when the stored admin flag is set.
→ `AccessTokenService.getRoleForIdentity`, `User.admin`
> Verified — derives every role claim server-side; no request model carries a role.
```

A bare `### R1` means **derived** and **unverified** — description, regenerated whenever synthesis
re-runs. `authored` means binding intent: it survives regeneration untouched and keeps its id
forever, so a verdict can cite it across runs.

Each requirement's **anchor hash** covers the extracted facts its `→` symbols resolve to. When
those move the requirement is *stale*: a verified record is demoted and its verdict cleared,
because the code the judgement was about has changed.

## Commands

```
uv run systemmodel <repo>              # derive the model from the code (the default action)
```

| | |
|---|---|
| `--compare` | compare the model against the code; exit 1 if stale |
| `--adopt` | adopt the prose in `intent.md` as requirement records |
| `--verify` | judge each authored requirement against the code, recording a verdict |
| `--enforce` | exit 1 if any authored requirement is violated or unverified |
| `--brief` | emit a change brief for the unmet ones |
| `--remediate` | drive an agent from that brief, then re-derive and re-verify |

Scope and modifiers: `--all` (every detected repo), `--platform` (the L0 model), `--dry-run`,
`--adapter NAME`, and for `--remediate`: `--max-iters`, `--dangerous`, `--model`.

`derive`, `--compare` and `--enforce` are free and offline. `--adopt`, `--verify` and
`--remediate` call the `claude` CLI and need it on `PATH`.

Prerequisite: [uv](https://docs.astral.sh/uv/) — no install or venv activation needed.
Equivalent without it: `python -m systemmodel.derive <repo> ...`.

## Using it

**Keep it current.** `derive` after the code changes; wire `--compare` into CI or a pre-commit
hook. Synthesis is hash-gated, so re-deriving when nothing moved costs nothing.

```
uv run systemmodel "$(basename "$PWD")" --compare
```

**Change the system.** Write what you want in the repo's `intent.md`, in prose:

```markdown
## Desired updates

- Callers must be authenticated before any write
- R3 should also cover service accounts, not just human users
- drop R5 — that behaviour was removed last quarter
- promote token-issuance R2
```

Then `--adopt`. It works out what each entry means, allocates the id, resolves the anchors and
files the record in the right document — so there is no syntax to learn and no way to put one
somewhere nothing maintains it. Everything it applies becomes `authored`; processed entries move
to an `## Applied` log.

From there: `--verify` records a verdict per requirement, `--enforce` is the CI gate, and
`--brief` / `--remediate` close the gap when the code doesn't hold up.

**`--compare` and `--enforce` answer different questions.** The first asks *is the model stale?*,
the second *does the code keep its promises?* A fresh model can still violate a requirement, so
run both. With nothing authored, `--enforce` passes no matter what the code does — teeth come from
promoting requirements, not from running the command.

## Configuration

`platform.toml` at the model root is optional and hand-written. It sets policy that can't be
derived:

```toml
[repos]                       # correct a misclassified repo
platform = "experiment"

[policy]
aggregate_kinds = ["service"]                       # kinds the platform model measures
feature_kinds   = ["service", "library", "tester"]  # kinds that get a feature decomposition

[invariants]                  # bool signals every service must satisfy
security.enabled = true

[conventions]                 # value signals with a required value (quote them: "25")
test.runtime = "junit5"

[[exceptions]]                # one repo, one signal, with a reason
signal = "security.enabled"
repo   = "timeline"
reason = "Pure computation: no repository, no outbound client, nothing consumes it."

[[acknowledged_exposure]]     # an unauthenticated write reviewed and accepted
repo   = "event"
route  = "POST /event/{topic}"
reason = "Open ingest by design."
```

An exception excuses one repo from one signal and nothing else — it keeps its kind, is still
measured against every other requirement, and is reported rather than hidden. Acknowledged
exposures drop out of the "verify each is intended" list so a *new* one appears alone.

## Extending

A new target system is a new adapter, never a fork. An adapter implements `detect()` and
`extract_overview()`, plus optionally `extract_evidence()`, `anchor_facts()`, `wiring()`,
`classify()` and the platform-signal pair. The core owns frontmatter, the manifest, sections and
every requirement concern.

`anchor_facts()` is the important one: it indexes symbols to the facts they carry, and the hash of
what a requirement's anchors resolve to is what makes a semantic obligation drift-checkable.

## Limitations

- Extraction is regex/line based, so unconventional layouts may be partially captured.
- Repos matching no adapter (CI definitions, npm packages, this tool) aren't modelled at all.
- Feature decomposition is not stable across runs; slugs are sticky, so the first cut for a repo
  is the one you keep. Nothing prunes a superseded cut.
- Some anchors — config keys, test fixtures, genuinely prose references — resolve to nothing, and
  a requirement that anchors nothing can never go stale.
- A library's consumer count includes only repos naming it directly; transitive consumers are real
  but uncounted.
- Client libraries with no local checkout have their reached hosts hand-recorded in
  `core/clientlibs.py`; those entries are not verified against source.
- `--enforce` returns an exit code but isn't wired into the test-result pipeline. The gate is
  container-scoped and CI is repo-scoped, so it needs somewhere a whole-estate check can run.
