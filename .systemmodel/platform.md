---
level: L0
kind: platform
id: platform
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
  - draw
  - email
  - encryption
  - endpoint-tester
  - event
  - event-tester
  - expiration-tester
  - flare-api-portal
  - github
  - gradle-acceptance-plugin
  - health-dash
  - homepage
  - kraken
  - list
  - login
  - memo
  - memory
  - micronaut-security-utils
  - micronaut-utility-beans
  - monitor
  - network
  - platform
  - predict
  - project
  - prompt
  - prompt-tester
  - schedule
  - stripe
  - template-vue3
  - template-webapi
  - tenant
  - testing
  - threshold
  - timeline
  - trade
generator_version: 0.1.0
---

# Platform model (L0)

System-wide invariants and conventions, derived by aggregating the code of every repo in the platform. This is the platform peer of a repo's `.systemmodel/` — the `~/.claude` to a repo's project config.

- **Repos scanned:** 50
- **Aggregated over:** 38 repos of kind service
- **Adapters:** micronaut_groovy

## Repo census

Each repo's kind (service invariants apply only to services). Kinds are derived from code; `platform.toml` can override.

- **experiment** (2): draw, predict, platform, cleo-frontend
- **library** (4): gradle-acceptance-plugin, kraken, micronaut-security-utils, micronaut-utility-beans
- **service** (38): action, active, alert, auth-provider, bigquery, billing, bucket, candlebot, catalog, changelog, chat, data, datastore, deploy, email, encryption, event, flare-api-portal, github, health-dash, homepage, list, login, memo, memory, monitor, network, project, prompt, schedule, stripe, tenant, testing, threshold, timeline, trade
- **template** (2): template-vue3, template-webapi
- **tester** (4): endpoint-tester, event-tester, expiration-tester, prompt-tester
