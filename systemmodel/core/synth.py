"""Synthesis: turn an evidence bundle into requirements-level prose via an agent.

Deterministic extraction can only surface what is syntactically present — it can see
`FolderController` and `ShareToken` but cannot conclude "folders are shareable to recipients
without accounts". That leap is what this module buys, and it is the only part of the pipeline
that is non-deterministic.

Two properties keep it trustworthy. Every synthesized statement is *anchored* to the classes and
files it came from, and a region is only regenerated when its evidence hash moves — so output is
diff-stable, re-runs are free, and the set of regenerated regions is the change stream.

Like `core/auto`, this shells out to the `claude` CLI. Unlike `auto`, it runs strictly read-only:
synthesis must never edit the target repo.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from systemmodel.core.evidence import Evidence
from systemmodel.core.locate import model_root
from systemmodel.core.overlay import split_regions, synth_requests
from systemmodel.core.requirements import REQUIREMENTS_REGION, reconcile
from systemmodel.core.schema import Node

TIMEOUT_SECONDS = 420

_READ_ONLY_TOOLS = "Read,Grep,Glob"
_FORBIDDEN_TOOLS = "Write,Edit,MultiEdit,NotebookEdit,Bash,WebFetch,WebSearch"

_RULES = """You are writing one section of a system model for the repo `{repo}`.

The model exists so a human can understand this system at a glance, and so an agent can act on it
without re-reading everything. Its value is inversely proportional to how easily the reader could
have grepped the same thing.

Read the actual code before writing. The JSON below is a starting scaffold, not the whole truth --
open the controllers, services, and domain types it names and work out what this system is really
for.

Hard rules:
- NEVER state dependency versions, framework names, build tooling, JDK versions, packaging, or
  naming conventions. Those are greppable and worthless here.
- NEVER restate the route table or enumerate HTTP verbs and paths.
- Write about behaviour and obligations, not implementation.
- Claim nothing you did not find in the code.
- Output ONLY the section body. No preamble, no headings, no code fences, no sign-off.

Dependencies — read this before calling anything self-contained:
`shared.wiring` carries three separate facts and you must honour all three.
- `calls` are services named by a literal URL in this repo's own source.
- `library_calls` are services reached through a shared client library, so the hostname never
  appears in this repo. THEY ARE REAL DEPENDENCIES. A repo using `Repository` talks to the
  datastore; one using a `SecureHttpClient` depends on the auth service. Grepping the source
  will not show them.
- `consumed_by` is who depends on THIS service.

So: never write that a service is self-contained, standalone, a leaf, or that it "calls nothing"
unless BOTH `calls` and `library_calls` are empty. Never write that nothing depends on it unless
`consumed_by` is empty. If `consumed_by` is large this service is infrastructure — say so, and
say roughly how much of the platform rests on it.

{section_rules}

Evidence scaffold:
```json
{evidence}
```
"""

_PURPOSE_RULES = """Write the PURPOSE section.

HARD BUDGET: 55 words maximum. This is a glance, not a briefing. Going over is a failure.

Say what this system is for, and what makes it distinct from its siblings on this platform. Lead
with the single thing a newcomer most needs to know. If other services depend on it, say so.

Exactly the right length and altitude:
Consumer photo galleries. The only service on the platform with end-user accounts rather than app
identities -- everything else authenticates services; this authenticates people. Leaf, not
infrastructure: it consumes auth, blob storage and the event bus, and nothing depends on it."""

_REQUIREMENTS_RULES = """Write the REQUIREMENTS section: at most 7 numbered requirements.

HARD BUDGET: each requirement is ONE sentence of 30 words maximum, then its anchor line. Going
over is a failure. You are writing the sentence someone remembers, not the paragraph that proves
you read everything. Detail belongs in the code; the anchor is how they find it.

Format each as exactly two lines:
R<n>. <one sentence, <=30 words, stating an obligation the system must meet>
    -> <real class, file, or host names this rests on>

The `->` anchor line is mandatory and must name things that genuinely exist in the evidence or
the code you read. It is what lets an agent act on the requirement.

Right length:
R1. Ownership is decided from the signed token, never from cookies, because the identity cookies
    this service sets are readable and writable by the browser.
    -> ImageController.isOwnerOrAdmin, FolderController.isCreatorOrAdmin, Authentication

Too long -- do not do this:
R1. Every read and write of photo content, albums, and comments must be gated on a signed session
    identity, and the system must decide ownership from the JWT alone, because the admin and
    user_name cookies it sets are deliberately not HttpOnly and therefore client-writable, so the
    ownership helpers read roles and name from Authentication rather than from a cookie, and the
    uploader and comment author are likewise stamped from the authenticated identity.

Order by importance to a reader, not by package layout. Prefer fewer, sharper requirements;
anything that merely restates CRUD is not worth a line."""

_FEATURES_RULES = """Decompose this repo into FEATURES.

A feature is a capability with intent -- something the system does for someone. It is NOT a class,
a controller, or a package. "test-result fan-out" is a feature; "EventWebhookController" is not.
Aim for 3-6. If the repo really only does one thing, say so with one feature rather than inventing
structure that is not there.

Output format, and nothing else:

## slug-in-kebab-case -- Human Readable Title
One sentence on what this feature is for and who needs it.
R1. <one obligation this feature must meet, <=30 words>
    -> <real class, member, file or host names it rests on>
R2. <...>

Rules:
- The slug is lowercase, kebab-case, and stable: name the capability, not the implementation, so
  it survives a refactor.
- 2-5 requirements per feature. These are the finer-grained obligations that would bury the
  headline requirements in the overview -- do not restate those.
- The `->` anchor line is mandatory and must name things that genuinely exist in the code. Prefer
  `Type.member` over `Type` where one member is what the obligation actually rests on; it makes
  the requirement precise about what change should reopen it.
- Same hard rules as elsewhere: no versions, no frameworks, no route tables, no restating
  structure a reader could grep."""

_SECTION_RULES = {"purpose": _PURPOSE_RULES, "requirements": _REQUIREMENTS_RULES,
                  "features": _FEATURES_RULES}

_VERIFY_PROMPT = """You are checking whether the repo `{repo}` SATISFIES one authored requirement.

This is a human-written obligation the system is supposed to meet. Your job is to find out
whether the code actually meets it — not whether it looks like it might.

REQUIREMENT {rid}:
{body}

It rests on: {anchors}

Method:
- Open those anchors and read the real code paths. Follow the calls; do not stop at a
  reassuring method name.
- Argue the other side first. Try to find an input, a path, or a caller for which the
  requirement does NOT hold. A requirement is satisfied only if that attempt fails.
- An obligation partially met is `violated`. "Handled in the common case" is not satisfied.
- Judge only this requirement. Other problems in the code are out of scope.

If the anchors do not exist, or you cannot reach a defensible conclusion from the code, say
`unclear` — do not guess in either direction.

Output EXACTLY two lines, nothing else:
VERDICT: satisfied|violated|unclear
FINDING: <one sentence, <=25 words, naming the specific code that decided it>"""


# Unanchored on purpose: an agent that adds a preamble despite being told not to has still
# answered, and throwing that away as "unclear" would discard a real verdict over formatting.
_VERDICT = re.compile(r"VERDICT:\s*(satisfied|violated|unclear)", re.IGNORECASE)
_FINDING_LINE = re.compile(r"FINDING:\s*(.+)")


def verify(repo: Path, requirement, *, model: str | None = None,
           on_log=print) -> tuple[str | None, str | None]:
    """Ask an agent whether the code satisfies one requirement.

    Returns `(state, finding)`, or `(None, None)` when no defensible verdict was reached — an
    unclear answer leaves the record untouched rather than recording a guess as evidence.
    """
    if not available():
        on_log("warning: `claude` CLI not found — cannot verify.")
        return None, None
    prompt = _VERIFY_PROMPT.format(
        repo=repo.name, rid=requirement.id, body=requirement.body,
        anchors=", ".join(requirement.anchors) or "(no anchors given)",
    )
    raw = _invoke(repo, prompt, model)
    if raw is None:
        return None, None
    verdict = _VERDICT.search(raw)
    finding = _FINDING_LINE.search(raw)
    if not verdict:
        on_log(f"    warning: no verdict found in the reply: {raw.strip()[:160]!r}")
        return None, None
    decision = verdict.group(1).lower()
    if decision == "unclear":
        return None, (finding.group(1).strip() if finding else None)
    from systemmodel.core.requirements import VERIFIED, VIOLATED
    return (VERIFIED if decision == "satisfied" else VIOLATED,
            finding.group(1).strip() if finding else None)


def _command(model: str | None) -> list[str]:
    cmd = [
        "claude", "-p",
        "--allowedTools", _READ_ONLY_TOOLS,
        "--disallowedTools", _FORBIDDEN_TOOLS,
    ]
    if model:
        cmd += ["--model", model]
    return cmd


def _clean(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        lines = lines[1:]
        while lines and not lines[-1].strip().startswith("```"):
            lines.pop()
        if lines:
            lines.pop()
        body = "\n".join(lines).strip()
    return body


def _invoke(repo: Path, prompt: str, model: str | None) -> str | None:
    try:
        proc = subprocess.run(
            _command(model), cwd=repo, input=prompt, text=True,
            encoding="utf-8", capture_output=True, timeout=TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    body = _clean(proc.stdout or "")
    return body or None


def _prompt(repo_name: str, section: str, evidence: Evidence) -> str:
    return _RULES.format(
        repo=repo_name,
        section_rules=_SECTION_RULES.get(section, ""),
        evidence=evidence.as_prompt_json(section),
    )


def decompose(repo: Path, evidence: Evidence, index: dict[str, dict], *,
              model: str | None = None, on_log=print) -> tuple[list, str, bool]:
    """Resolve this repo's feature decomposition, calling the agent only when the code moved.

    Returns `(features, evidence_stamp, regenerated)`. One call per repo, not one per feature:
    the agent has to read the whole repo to cut it sensibly either way, so per-feature calls
    would multiply cost by the branching factor and buy nothing.
    """
    from systemmodel.core.features import load, parse_decomposition, reconcile

    prior = load(model_root(repo))
    stamp = evidence.section_hash("requirements")

    def keep(reason: str | None = None) -> tuple[list, str, bool]:
        if reason:
            on_log(reason)
        return reconcile(prior, list(prior.values()), index), stamp, False

    if prior and _recorded_decomposition(repo) == stamp:
        return keep()
    if not available():
        return keep("warning: `claude` CLI not found — feature decomposition not refreshed.")

    on_log("  synthesizing features …")
    body = _invoke(repo, _prompt(repo.name, "features", evidence), model)
    if body is None:
        return keep("  warning: feature decomposition failed; keeping what is on disk.")
    fresh = parse_decomposition(body)
    if not fresh:
        return keep("  warning: decomposition returned nothing parseable; keeping disk state.")
    return reconcile(prior, fresh, index), stamp, True


def _recorded_decomposition(repo: Path) -> str | None:
    """The evidence hash the on-disk decomposition was built from, from any feature doc."""
    directory = model_root(repo) / "features"
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.md")):
        stamp = re.search(r"<!-- decomposition evidence=([0-9a-f]*) -->",
                          path.read_text(encoding="utf-8"))
        if stamp:
            return stamp.group(1)
    return None


def _prior_regions(repo: Path, node: Node) -> dict:
    on_disk = model_root(repo) / node.path
    if not on_disk.is_file():
        return {}
    return split_regions(on_disk.read_text(encoding="utf-8"))[2]


def available() -> bool:
    return shutil.which("claude") is not None


def resolve(repo: Path, nodes: list[Node], evidence: Evidence, *,
            model: str | None = None, on_log=print,
            anchor_index: dict[str, dict] | None = None
            ) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Resolve every synth region to prose, generating only what the evidence hash invalidated.

    Returns `(prose_by_node_path, regenerated_ids)`. Prose is returned as an overlay rather than
    merged into `Node.body` so it never enters the content hash.
    """
    prose_by_path: dict[str, dict[str, str]] = {}
    regenerated: list[str] = []
    can_generate = available()
    warned = False

    for node in nodes:
        requests = synth_requests(node.body)
        if not requests:
            continue
        prior = _prior_regions(repo, node)
        resolved: dict[str, str] = {}
        for region_id, current_evidence in requests.items():
            known = prior.get(region_id)
            prior_prose = known.prose if known else ""

            def keep(prose: str | None) -> None:
                """Reuse prior content, still normalizing a requirements region.

                Migration from the legacy prose format has to happen on the reuse path too: a
                repo whose evidence never moves would otherwise keep its old shape forever.
                """
                if prose is None:
                    return
                resolved[region_id] = (reconcile(prose, index=anchor_index)
                                       if region_id == REQUIREMENTS_REGION else prose)

            if known and known.evidence == current_evidence and not known.is_placeholder():
                keep(prior_prose)
                continue
            if not can_generate:
                if not warned:
                    on_log("warning: `claude` CLI not found — keeping existing prose, "
                           "synthesized sections will not refresh.")
                    warned = True
                keep(prior_prose if known else None)
                continue
            on_log(f"  synthesizing {node.path}:{region_id} …")
            body = _invoke(repo, _prompt(repo.name, region_id, evidence), model)
            if body is None:
                on_log(f"  warning: synthesis failed for {region_id}; keeping prior prose.")
                keep(prior_prose if known else None)
                continue
            # Fresh description is merged into preserved intent rather than replacing it, so a
            # promoted requirement is never lost to a re-synthesis.
            resolved[region_id] = (reconcile(prior_prose, body, index=anchor_index)
                                   if region_id == REQUIREMENTS_REGION else body)
            regenerated.append(f"{node.path}:{region_id}")
        if resolved:
            prose_by_path[node.path] = resolved

    return prose_by_path, regenerated
