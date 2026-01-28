# RollingThunder — Deployment Model (Authoritative) #

This document defines ***how code is deployed*** to RollingThunder nodes and ***why***
certain files are owned by `root` versus the normal operating user (`spiff`).

It exists to prevent:
- accidental overwrites
- privilege confusion
- brittle manual deployment
- “forgot to push a file” failures
- architectural drift between repo layout and runtime layout

If deployment behavior changes without updating this document, the change is considered **incomplete**.
---
## 1. Deployment Philosophy ##
RollingThunder is an **appliance-style system**, not a general-purpose workstation.

That means:
- always-on services must be protected from accidental modification
- boot behavior must be deterministic
- deployment steps must be repeatable and explicit
- visibility and safety beat convenience

Convenience is secondary to **stability and safety**.
---
## 2. Repository Is the Source of Truth ##
For any managed subtree:
```
***The repository defines the intended state.***
```
Deployment is the process of synchronizing that state onto a target node.

Deployment scripts must not:
- enumerate individual files manually (brittle, easy to forget)
- rely on “remembering to update the push script”
- silently skip modified files
---
## 3. Ownership Model (Non-Negotiable) ##
RollingThunder uses a **two-tier ownership model**.

### 3.1 Root-Owned (Protected) ###
Files that:
- start automatically
- run unattended
- affect system-wide behavior
- define boot-time behavior or privileged execution

**Must be owned by** `root`.

These include:
***systemd unit files***
```
/etc/systemd/system/*.service
```

Reason:
- defines boot-time behavior
- modifying units is a privilege boundary
- required by systemd security model

***Always-on service executables***
```
/opt/rollingthunder/services/
```

Examples:
- `ui_snapshot_api.py`
- `service_state_publisher.py`
- `node_presence_ingestor.py` (when promoted to always-on)
- future NOAA, Meshtastic, watchdog services

Reason:
- prevents accidental edits
- avoids partial writes during deploy
- stabilizes unattended operation

### 3.2 User-Owned (`spiff`) ###
Files that:
- are iterated on frequently
- do not define boot behavior directly
- are safe to edit live (breakage is visible and recoverable)

**Should be owned by** `spiff`.

These include:
```
/opt/rollingthunder/nodes/
├── rt-controller/
├── rt-display/
```

As well as:
```
/opt/rollingthunder/config/
 /opt/rollingthunder/tools/
 UI HTML / JS assets
```

Reason:
- faster iteration
- lower risk
- supports the appliance model without blocking development speed

---
## 4. Repository Layout vs Runtime Layout ##
### 4.1 Repository (Logical Ownership) ###
The repo is organized by **node and responsibility**:
```
nodes/
├── rt-controller/
│   ├── services/
│   │   └── ui_snapshot_api.py
│   ├── systemd/
│   └── ...
```

This expresses **ownership and intent**, not execution context.

### 4.2 Runtime (Operational Simplicity) ###
At runtime, always-on services are centralized:
```
/opt/rollingthunder/
├── services/        # root-owned executables
├── nodes/           # spiff-owned node logic
├── config/
├── tools/
```

This separation is intentional:
- repo layout optimizes for understanding
- runtime layout optimizes for stability

They are not required to mirror each other.
---
## 5. Deployment Mechanism (Preferred) ##
RollingThunder deployments are based on subtree synchronization, not manual file lists.

### 5.1 Subtree Sync (Authoritative) ###
Deployment scripts should sync directories (subtrees) so:
- new files deploy automatically
- modified files cannot be forgotten
- drift becomes visible via rsync output
- scripts don’t need changes when files are added

The rsync itemized output is treated as a **deployment visualizer**, not noise.
---
## 6. Root-Owned File Installation Rules (Authoritative) ##
***Rule 1 — Never SCP directly into root-owned paths***
This is **not allowed**:
```
scp file.py user@host:/opt/rollingthunder/services/file.py   ❌
```

Reason:
- bypasses ownership boundary
- risks partial writes
- defeats appliance protections

***Rule 2 — Root-owned files must be staged (atomic pattern)***
Root-owned deployments must use a staging/install pattern (commonly via `/tmp`)
so replacement is explicit and privilege escalation is deliberate.

Canonical pattern:
```
scp file.py user@host:/tmp/file.py
ssh user@host "sudo mv /tmp/file.py /opt/rollingthunder/services/file.py"
ssh user@host "sudo chown root:root /opt/rollingthunder/services/file.py"
ssh user@host "sudo chmod 755 /opt/rollingthunder/services/file.py"
```

This ensures:
- atomic replacement
- correct ownership
- explicit privilege boundary crossing
In practice, scripts may implement this via:
- push_root_file helpers, or
- staging directories + sudo rsync installs

***Rule 3 — systemd must be reloaded after unit changes***
After modifying `/etc/systemd/system/*.service`:
```
sudo systemctl daemon-reload
sudo systemctl restart <service>
```

Skipping daemon-reload is considered a deployment error.
---
## 7. Environment Files Under `/etc/rollingthunder` (Authoritative) ##
RollingThunder uses `.env`-style files under:
```
/etc/rollingthunder/
```

to hold **node-local operational configuration** (root-owned).

Examples:
- Redis host overrides
- MQTT broker location
- unit-to-service mappings
- safety or rate-limit tuning

***Rule — Install If Missing (Mandatory Default)***
Deployment scripts must not blindly overwrite existing environment files in
`/etc/rollingthunder/`.

Instead:
- The repo contains *.env.template files as defaults and documentation
- Deploy installs an env file **only if it does not already exist**
- Existing env files are preserved verbatim
Rationale:
- env files may legitimately diverge per node
- blind overwrites erase node-specific settings
- silent configuration changes violate the appliance model

***Intentional Overrides (Explicit Only)***
If an env file must be replaced, it must be done intentionally:
- manual root edit on the node, or
- an explicit deploy override mode (future enhancement)
Silent overwrites are considered a ***deployment bug***.
---
## 8. DRY_RUN Mode (Non-Negotiable) ##
All deployment scripts must support:
```
DRY_RUN=1
```
## DRY_RUN Guarantees ##
When DRY_RUN=1:
- ***No files are modified on the target***
- ***No root-owned files are installed***
- ***No services are restarted***
- ***No systemd reloads occur***
- ***No env files are written***
- ***No deployed-commit stamp is written***
###What DRY_RUN Does Do###
- Runs rsync with `--dry-run`
- Prints a complete, itemized change list
- Acts as a ***deployment visualizer***
- Shows exactly what *would* change

If a script claims DRY_RUN but still mutates the system, that is a bug.
---
## 9. Deletion Semantics##
Deletion is **opt-in** and deliberate.
By default:
- deployments ***do not delete*** files on the target

Rationale:
- protects against accidental removal of local-only artifacts
- avoids “oops we deleted the wrong thing” during early iteration

If deletion is enabled:
- it must be explicit (`--delete`)
- it must apply only to directories that are fully managed by deploy
- it must be documented in the deployment script

Deletion without clear ownership is forbidden.
---
## 10. Deployment State Recording ##
After a successful (non-dry) deployment:
- the deployed Git commit hash should be recorded on the target node, typically:
```
/opt/rollingthunder/.deploy/DEPLOYED_COMMIT
```

This enables:
- fast correlation between runtime behavior and source state
- postmortem debugging
- confidence that the target matches expectations

### 10.1 Deploy Drift Reporting (Authoritative) ###
RollingThunder includes a ***read-only drift visualizer*** that reports what code/config is actually running on each node and flags mismatches against the repo’s expected deployment.

This exists to prevent:
- “it works on that Pi” mysteries
- silent partial deploys
- unit file drift
- root-owned executable drift

### 10.1.1 Node Deploy Report (Publisher → MQTT) ###
Each node may publish a bounded deploy report on MQTT:
- Topic:
```
rt/deploy/report/<node_id>
```

- Payload schema:
```
schema: "deploy.report.v1"
```

- Required fields:
   - `node_id` (string)
   - `role` (string)
   - `ts_ms` (number; publisher timestamp)
   - `deployed_commit` (string; from /opt/rollingthunder/.deploy/DEPLOYED_COMMIT)
   - `git_head` (string|null) (optional; if node has a git checkout)
   - `dirty` (boolean|null) (optional)
   - `units` (object) — map of unit_name -> "sha256:<hex>" for installed unit files
      - Example key: `"rt-ui-snapshot-api.service": "sha256:...."`

Notes:
- Reports are bounded (no unbounded logs, no large file dumps).
- Reports are read-only and informational (no control actions).

### 10.1.2 Controller Ingestion (MQTT → Redis) ###
The controller ingests deploy reports and stores them in Redis as strings:
- Key:
```
rt:deploy:report:<node_id>
```

- Value:
```
JSON string of the deploy report payload (schema deploy.report.v1)
```
Ownership:
- ***Only the controller*** writes rt:deploy:report:*.

### 10.1.3 UI Exposure (Read-Only HTTP) ###
The controller exposes a read-only API endpoint for the UI:
- Endpoint:
```
/api/v1/ui/deploy
```

The payload includes:
- The controller’s expected deployed commit (what the repo believes should be deployed)
- Per-node deploy report (latest)
- A derived drift classification:
   - `state: ok|warn|bad`
   - `reasons: [ ... ]` (e.g., `deployed_commit_mismatch, report_stale, unit_hash_mismatch`)

The UI must not compute drift on its own; it only renders controller-provided drift output.

### 10.1.4 Drift Signals (Authoritative) ###
Drift is evaluated from:
- deployed_commit mismatch vs expected
- stale/missing report (report_age_sec beyond threshold)
- unit file hash mismatches for relevant units
- optional: root-owned executable hash mismatches (if reported)

### 10.1.5 Deployment Interaction ###
Deployment scripts must ensure:
- `/opt/rollingthunder/.deploy/DEPLOYED_COMMIT` is updated after a successful deploy
- deploy report publishers are installed/enabled where applicable
- DRY_RUN does not write stamps, restart services, or publish reports

---
## 11. SSH Key-Based Deployment (Recommended) ##

Deployment should use a dedicated SSH key from the dev machine to avoid password prompts
and to support deterministic scripts.

Example:
```
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_rt_deploy -C "rollingthunder-deploy"
ssh-copy-id -i ~/.ssh/id_ed25519_rt_deploy.pub spiff@rt-controller
```
---
## 12. Deployment Scripts (Preferred Mechanism) ##
Manual deployment is acceptable during early development, but ***deployment scripts are the preferred mechanism.***

Scripts should:
- encode ownership rules
- handle staging / root-owned installs correctly
- restart only the relevant services
- optionally perform smoke checks
- provide DRY_RUN drift visibility

Examples:
```
deploy/push_rt_controller.sh
deploy/push_rt_display.sh
```

These scripts serve as:
- automation
- documentation
- enforcement of architectural intent
---
## 13. Security & Reliability Rationale ##
This model ensures:
- accidental shell mistakes cannot rewrite boot services
- compromised user account cannot silently alter appliance behavior
- services behave the same after reboot as before
- contributors understand why boundaries exist

This is not overengineering. This is how **reliable systems stay reliable.**
---
## 14. When This Document Must Be Updated ##
Update this document when:
- a new always-on service is added
- ownership rules change
- runtime layout changes
- deployment process changes materially
- DRY_RUN semantics change
- deletion behavior changes
- drift reporting schema/keys/endpoints change materially

If a future reader cannot answer:
    “What exactly does deployment guarantee, and why are these boundaries here?”
then the documentation is incomplete.
---
End of Deployment Model