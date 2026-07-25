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

import shutil
import subprocess
from pathlib import Path

from systemmodel.core.evidence import Evidence
from systemmodel.core.locate import model_root
from systemmodel.core.overlay import split_regions, synth_requests
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

_SECTION_RULES = {"purpose": _PURPOSE_RULES, "requirements": _REQUIREMENTS_RULES}


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


def _prior_regions(repo: Path, node: Node) -> dict:
    on_disk = model_root(repo) / node.path
    if not on_disk.is_file():
        return {}
    return split_regions(on_disk.read_text(encoding="utf-8"))[2]


def available() -> bool:
    return shutil.which("claude") is not None


def resolve(repo: Path, nodes: list[Node], evidence: Evidence, *,
            model: str | None = None, on_log=print) -> tuple[dict[str, dict[str, str]], list[str]]:
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
            if known and known.evidence == current_evidence and not known.is_placeholder():
                resolved[region_id] = known.prose
                continue
            if not can_generate:
                if not warned:
                    on_log("warning: `claude` CLI not found — keeping existing prose, "
                           "synthesized sections will not refresh.")
                    warned = True
                if known:
                    resolved[region_id] = known.prose
                continue
            on_log(f"  synthesizing {node.path}:{region_id} …")
            body = _invoke(repo, _prompt(repo.name, region_id, evidence), model)
            if body is None:
                on_log(f"  warning: synthesis failed for {region_id}; keeping prior prose.")
                if known:
                    resolved[region_id] = known.prose
                continue
            resolved[region_id] = body
            regenerated.append(f"{node.path}:{region_id}")
        if resolved:
            prose_by_path[node.path] = resolved

    return prose_by_path, regenerated
