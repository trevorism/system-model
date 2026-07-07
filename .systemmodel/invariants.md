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

Constraints (nearly) every service's code satisfies. Outliers are drift.

- **Micronaut security enabled:** 35/38 repos  ⚠ outliers: changelog, network, timeline
- **HTTPS enforced (App Engine secure:always):** 36/38 repos  ⚠ outliers: monitor, trade
- **HTTP→HTTPS redirect:** 38/38 repos
- **Coverage gate wired into build:** 37/38 repos  ⚠ outliers: platform
- **Liveness /ping endpoint:** 38/38 repos
