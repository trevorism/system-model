---
level: L0
kind: invariant
id: platform-invariants
adapter: micronaut_groovy
status: derived
derived_from:
  - action
  - active
  - alert
  - auth-provider
  - bigquery
  - billing
  - bucket
  - candlebot
  - catalog
  - changelog
  - chat
  - cleo-frontend
  - data
  - datastore
  - deploy
  - email
  - encryption
  - event
  - flare-api-portal
  - github
  - health-dash
  - homepage
  - list
  - login
  - memo
  - memory
  - monitor
  - network
  - platform
  - project
  - prompt
  - schedule
  - stripe
  - tenant
  - testing
  - threshold
  - timeline
  - trade
generator_version: 0.1.0
---

# Platform invariants (L0)

**REQUIRED** lines are authored intent — violations are drift. Other lines are the observed norm across services, not a requirement.

- **Micronaut security enabled:** REQUIRED `yes` — 36/38 conform  ⚠ violations: network, timeline
- **HTTPS enforced (App Engine secure:always):** REQUIRED `yes` — 38/38 conform
- **HTTP→HTTPS redirect:** REQUIRED `yes` — 38/38 conform
- **Coverage gate wired into build:** REQUIRED `yes` — 37/38 conform  ⚠ violations: platform
- **Liveness /ping endpoint:** REQUIRED `yes` — 38/38 conform
- **Micronaut BOM/plugin versions aligned:** REQUIRED `yes` — 26/38 conform  ⚠ violations: bucket, candlebot, catalog, cleo-frontend, datastore, encryption, github, memory, network, platform, stripe, timeline
