---
level: L0
kind: convention
id: platform-conventions
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

# Platform conventions (L0)

**REQUIRED** lines are authored intent — violations are drift. Other lines report the observed norm.

- **JDK version:** REQUIRED `25` 
- **Micronaut version (BOM):** REQUIRED `5.0.2` 
- **Micronaut version (application plugin):** REQUIRED `5.0.2` 
- **Unit test runtime:** REQUIRED `junit5`
- **Coverage minimum:** expected `0.4` 
