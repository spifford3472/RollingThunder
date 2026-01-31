# STATE_OWNERSHIP.md #
*RollingThunder state is shared via Redis. To keep it deterministic and debuggable, each key/field has an “owner” (the one writer that is allowed to set it authoritatively). Other components may read it, but should not rewrite it unless explicitly documented.*

## Namespaces ##
- Default namespace: `rt`
- Node keys: `rt:nodes:<node_id>`
- System keys: `rt:system:*`
- Service keys: `rt:services:<service_id>`

## Field ownership rules ##

### `rt:nodes:<node_id>` ###
**Primary purpose:** one hash per node, used by UI + health logic.

**Authoritative writers**
- **rt-controller heartbeat / controller health loop**
  - Owns: `last_seen_ms`, `last_update_ms` (when it writes), `hostname`, `ip`, `publisher_error` (when it is the source), and any controller-self liveness metadata.
  - May write: `id`, `role` (stable identity fields)
  - heartbeat must not set status

- **rt-node-presence-ingestor**
  - Owns: ingestion/normalization of *remote node presence* messages into `rt:nodes:<remote_node_id>`
  - Owns (for ingested nodes): `status`, `age_sec`, and any fields derived directly from incoming MQTT presence.
  - For `rt-controller` record: should ideally avoid rewriting stable identity fields; if it refreshes `status`/`age_sec`, that is acceptable as long as no other writer asserts conflicting values.

**Conventions**
- `status` values:
  - `online` = observed recently (fresh presence/heartbeat)
  - `offline` = expired (set by a single owner, if implemented)
  - `stale` = stale
- `age_sec`:
  - derived value (seconds since last_seen/last_update); should be owned by whichever component computes it.

### `rt:system:health` ###
**Primary purpose:** controller health snapshot
- Owner: **controller health publisher**
- Fields: `node_id`, `hostname`, `boot_ms`, `last_seen_ms`, `uptime_sec`, `pid`, `python`, `schema_id`, `schema_version`, `redis_ok`, `mqtt_ok`

### `rt:system:info` ###
**Primary purpose:** lightweight “about the system” + liveness
- Owner: **controller bootstrap + controller health publisher**
- Fields: schema metadata and counts on boot; `last_seen_ms` / `uptime_sec` as it runs.

### `rt:services:<service_id>` ###
**Primary purpose:** state for each logical service (running/unknown/stopped)
- Owner:
  - Static metadata on boot: **controller bootstrap** (`state_publisher.publish_initial_state`)
  - Runtime state changes: **service state publisher**
- Fields:
  - Static: `id`, `scope`, `ownerNode`, `startPolicy`, `stopPolicy`
  - Runtime: `state`, `last_update_ms`

### `rt:deploy:report:<node_id>` ###
**Primary purpose:** per-node deploy/runtime report used by drift visualizer.

**Authoritative writers**
- **rt-controller deploy_reporter** (for `rt-controller` only): writes `rt:deploy:report:rt-controller`
- **rt-node-presence-ingestor** (for non-controller nodes): ingests MQTT deploy reports and writes `rt:deploy:report:<remote_node_id>`

**Notes**
- Value is a JSON string (schema `deploy.report.v1`)
- TTL is enforced to allow “stale report” detection

## Debugging: identify writers quickly ##

### Redis monitor patterns ###
Use:
```bash
redis-cli monitor | grep -E 'rt:nodes:|rt:system:|rt:services:|HSET|HMSET'
```
Typical patterns:
- Presence ingestor:
 - frequent `HGETALL rt:nodes:<id>` then `HSET ... status ... age_sec ... last_update_ms ...`
- Heartbeat / controller health:
 - periodic `HSET rt:nodes:rt-controller ... last_seen_ms ...`
 - periodic `HSET rt:system:health ...`
 - periodic `HSET rt:system:info ...`
- Service state publisher:
 - periodic `HSET rt:services:<id> state running last_update_ms ...`

## Policy ##
If two components write the same field with different meanings, fix it:
- pick a single owner
- document it here
- enforce it in code (don’t “helpfully” rewrite fields you don’t own)

---
**End of STATE_OWNERSHIP.md Document**
