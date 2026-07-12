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

- **JDK version:** REQUIRED `25` — 32/38 conform (`25`×32, `17`×5, `21`×1)  ⚠ catalog=`21`, cleo-frontend=`17`, monitor=`17`, network=`17`, platform=`17`, timeline=`17`
- **Micronaut version (BOM):** REQUIRED `5.0.2` — 21/38 conform (`5.0.2`×21, `5.0.0`×12, `4.5.0`×1, `4.5.1`×1, `4.7.1`×1, `4.7.4`×1, `4.9.4`×1)  ⚠ action=`5.0.0`, alert=`5.0.0`, bigquery=`5.0.0`, catalog=`4.9.4`, chat=`5.0.0`, cleo-frontend=`4.5.0`, deploy=`5.0.0`, email=`5.0.0`, event=`5.0.0`, flare-api-portal=`5.0.0`, list=`5.0.0`, network=`4.7.1`, platform=`4.7.4`, project=`5.0.0`, tenant=`5.0.0`, threshold=`5.0.0`, timeline=`4.5.1`
- **Micronaut version (application plugin):** REQUIRED `5.0.2` — 14/38 conform (`5.0.0`×19, `5.0.2`×14, `4.3.8`×3, `4.4.4`×1, `4.5.4`×1)  ⚠ action=`5.0.0`, alert=`5.0.0`, bigquery=`5.0.0`, bucket=`5.0.0`, candlebot=`5.0.0`, catalog=`4.5.4`, chat=`5.0.0`, cleo-frontend=`4.3.8`, datastore=`5.0.0`, deploy=`5.0.0`, email=`5.0.0`, encryption=`5.0.0`, event=`5.0.0`, flare-api-portal=`5.0.0`, github=`5.0.0`, list=`5.0.0`, memory=`5.0.0`, network=`4.3.8`, platform=`4.4.4`, project=`5.0.0`, stripe=`5.0.0`, tenant=`5.0.0`, threshold=`5.0.0`, timeline=`4.3.8`
- **Unit test runtime:** REQUIRED `junit5` — 38/38 conform (`junit5`×38)
- **Coverage minimum:** expected `0.4` (observed norm) (`0.4`×10, `0.1`×6, `0.6`×6, `0.5`×5, `0.0`×4, `0.2`×1, `0.3`×1, `0.45`×1, `0.7`×1, `0.8`×1, `0.9`×1, `—`×1)  ⚠ action=`0.1`, active=`0.0`, alert=`0.5`, auth-provider=`0.45`, bigquery=`0.1`, bucket=`0.0`, candlebot=`0.7`, catalog=`0.6`, chat=`0.9`, cleo-frontend=`0.0`, data=`0.6`, datastore=`0.5`, deploy=`0.6`, email=`0.5`, encryption=`0.6`, event=`0.1`, health-dash=`0.8`, homepage=`0.2`, list=`0.5`, login=`0.1`, memo=`0.3`, memory=`0.1`, monitor=`0.6`, prompt=`0.0`, schedule=`0.6`, threshold=`0.1`, trade=`0.5`  · unset: platform
