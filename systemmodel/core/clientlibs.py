"""Shared client libraries: which platform service a declared collaborator type actually reaches.

Most repos here depend on a service without ever naming it. They declare a `Repository` or a
`ChannelClient`, and the hostname lives inside the client library that supplies the type. Grepping
for hostnames therefore under-reports the graph badly — the point of this table is to recover
those edges.

The table is platform knowledge, not stack knowledge: a Groovy service and a Java library reach
`datastore.data.trevorism.com` through the same `Repository`, so both adapters read it from here.
An entry is only honoured when the repo also declares the artifact that supplies the type, so a
same-named class from somewhere else cannot invent an edge.

Entries whose library repo is checked out under the container are verified against its source by
`tests/test_java_library_adapter.py`; the rest (`secure-http-utils`, `reactions-client`,
`schedule-client`) are only published jars here, so their hosts stay hand-recorded.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LibraryClientTarget:
    provided_by_artifacts: tuple[str, ...]
    hardcoded_hosts: tuple[str, ...]


ARTIFACTS_EXPOSING_SECURE_HTTP_UTILS = (
    "secure-http-utils", "micronaut-utility-beans", "datastore-client", "event-client",
)
AUTH_HOST = "auth.trevorism.com"
DATASTORE_HOST = "datastore.data.trevorism.com"
EVENT_HOST = "event.data.trevorism.com"
SCHEDULE_HOST = "schedule.action.trevorism.com"

_DATASTORE_CLIENT = LibraryClientTarget(("datastore-client",), (DATASTORE_HOST, AUTH_HOST))
_EVENT_CLIENT = LibraryClientTarget(("event-client",), (EVENT_HOST,))
_SCHEDULE_CLIENT = LibraryClientTarget(("schedule-client",), (SCHEDULE_HOST, AUTH_HOST))
_TOKEN_MINTING_CLIENT = LibraryClientTarget(ARTIFACTS_EXPOSING_SECURE_HTTP_UTILS, (AUTH_HOST,))

LIBRARY_CLIENT_TARGETS: dict[str, LibraryClientTarget] = {
    "SecureHttpClient": _TOKEN_MINTING_CLIENT,
    "AppClientSecureHttpClient": _TOKEN_MINTING_CLIENT,
    "Repository": _DATASTORE_CLIENT,
    "FastDatastoreRepository": _DATASTORE_CLIENT,
    "PingingDatastoreRepository": _DATASTORE_CLIENT,
    "EventClient": _EVENT_CLIENT,
    "DefaultEventClient": _EVENT_CLIENT,
    "ChannelClient": _EVENT_CLIENT,
    "DefaultChannelClient": _EVENT_CLIENT,
    "AlertClient": LibraryClientTarget(("reactions-client",), ("alert.action.trevorism.com",)),
    "EmailClient": LibraryClientTarget(("reactions-client",), ("email.action.trevorism.com",)),
    "ListContentClient": LibraryClientTarget(("reactions-client",), ("list.data.trevorism.com",)),
    "TestErrorClient": LibraryClientTarget(("reactions-client",), ("testing.trevorism.com",)),
    "ScheduleService": _SCHEDULE_CLIENT,
    "DefaultScheduleService": _SCHEDULE_CLIENT,
}

# Artifacts this table can attribute an edge to, whether or not the repo supplying them is
# checked out locally. Used to tell "a library we model" from "a jar we only consume".
KNOWN_CLIENT_ARTIFACTS = frozenset(
    artifact
    for target in LIBRARY_CLIENT_TARGETS.values()
    for artifact in target.provided_by_artifacts
)


def client_type_name(declaration: str) -> str:
    """The bare type name from a field/param declaration (`private final Repository<App>`)."""
    without_generics = declaration.split("<", 1)[0].strip()
    words = without_generics.split()
    return words[-1] if words else ""


def hosts_reached(declared_types: set[str], declared_artifacts: set[str]) -> dict[str, list[str]]:
    """{host: [client types that carry the edge]} for the types this repo actually declares."""
    reached: dict[str, set[str]] = {}
    for type_name in declared_types:
        target = LIBRARY_CLIENT_TARGETS.get(type_name)
        if not target or declared_artifacts.isdisjoint(target.provided_by_artifacts):
            continue
        for host in target.hardcoded_hosts:
            reached.setdefault(host, set()).add(type_name)
    return {host: sorted(reached[host]) for host in sorted(reached)}


def artifacts_supplying(type_name: str) -> tuple[str, ...]:
    target = LIBRARY_CLIENT_TARGETS.get(type_name)
    return target.provided_by_artifacts if target else ()


def hosts_hardcoded_by(artifact: str) -> list[str]:
    """Hosts this table claims an artifact reaches, across every type it supplies."""
    hosts: set[str] = set()
    for target in LIBRARY_CLIENT_TARGETS.values():
        if artifact in target.provided_by_artifacts:
            hosts.update(target.hardcoded_hosts)
    return sorted(hosts)
