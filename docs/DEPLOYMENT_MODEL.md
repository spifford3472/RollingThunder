# RollingThunder — Deployment Model (Authoritative) #

This document defines the **authoritative deployment model** for the RollingThunder platform.

It exists to prevent:
- partial or inconsistent deployments
- “forgot to push a file” failures
- environment drift between nodes
- accidental side effects during testing
- deployment logic slowly turning into folklore

If deployment behavior changes without updating this document, the change is considered **incomplete**.

## 1. Purpose and Scope ##
RollingThunder deployment is designed to be:
- Deterministic — the same inputs produce the same remote state
- Auditable — changes are visible before they are applied
- Safe by default — dry runs cause zero side effects
- Boring — deployment should never be a source of uncertainty

This document describes:
- what is deployed
- how authority is defined
- how changes propagate
- what guarantees deployment provides

It does not describe step-by-step commands (see DEPLOYMENT.md for that).
---
## 2. Core Deployment Philosophy ##
### 2.1 Repository Is the Source of Truth ###
For any managed subtree:
      ***The repository defines the intended state.***

Deployment is a process of **synchronizing** that state onto a target node.

Deployment scripts must not:
- enumerate individual files manually
- rely on memory or convention
- silently skip modified files

### 2.2 Subtree Synchronization, Not File Lists ###
All RollingThunder deployments are based on **directory (subtree) synchronization**, not individual file copies.

Reasons:
- new files are deployed automatically
- modified files cannot be forgotten
- drift is visible via rsync output
- the script does not need to change when files are added

If a file is part of a managed subtree, it will be deployed.

### 2.3 Deployment Is Explicit About Ownership ###
Deployment respects **ownership boundaries**:
- **User-owned files**
  - deployed directly via rsync
  - typically live under /opt/rollingthunder/nodes/...
- **Root-owned files**
  - deployed explicitly via controlled install steps
  - systemd units
  - privileged service executables
  - configuration under /etc

Ownership is never inferred implicitly.
---

## 3. Node-Specific Deployment Models ##
### 3.1 rt-controller ###

***Managed Subtrees***
|***Repository Path***|***Target Path***|***Ownership***|
|---------------------|-----------------|---------------|
|nodes/rt-controller/ |	/opt/rollingthunder/nodes/rt-controller/ |	user|
|nodes/rt-controller/services/ |	/opt/rollingthunder/services/ |	root|

The controller deploy explicitly **excludes**:
- services/ from the node subtree (to avoid duplication)
- templates or ops files not intended for runtime

***Root-Owned Assets***
- systemd unit files
- service executables
- environment files under /etc/rollingthunder

These are installed deliberately, not via blind sync.

### 3.2 rt-display ###
***Managed Subtrees***
|Repository Path |	Target Path |	Ownership|
|----------------|--------------|------------|
|nodes/rt-display/ui/ |	/opt/rollingthunder/nodes/rt-display/ui/ |	user|
|nodes/rt-display/services/ |	/opt/rollingthunder/nodes/rt-display/services/ |	user|
|nodes/rt-display/ops/ |	/opt/rollingthunder/nodes/rt-display/ops/ |	user|

Deployment of these directories is automatic and complete.

***Root-Owned Assets***
- kiosk systemd unit
- UI service unit
- presence service unit

Units may originate from different repo locations but are installed explicitly.
---
## 4. DRY_RUN Mode (Non-Negotiable) ##
All deployment scripts support:
```
DRY_RUN=1
```

***DRY_RUN Guarantees***
When `DRY_RUN=1`:
- **No files are modified on the target**
- **No root-owned files are installed**
- **No services are restarted**
- **No systemd reloads occur**
- **No environment files are written**

***What DRY_RUN Does Do***
- Performs rsync with --dry-run
- Prints a complete, itemized change list
- Acts as a deployment visualizer
- Shows exactly what would change
If a script claims DRY_RUN but still mutates the system, that is a bug.
---
## 5. Deployment as a Visualization Tool ##
The rsync itemized output is treated as ***first-class signal***, not noise.

This output answers:
- what files are new
- what files changed
- what files differ only by metadata
- where drift exists

The human deploying the system should **always** be able to reason about changes before they occur.
---
## 6. Deletion Semantics ##
Deletion is **opt-in** and deliberate.

By default:
- deployments do not delete files on the target
- this protects against accidental removal of local-only artifacts
If deletion is enabled:
- it must be explicit (`--delete`)
- it must apply only to directories that are fully managed
- it must be documented in the deployment script
Deletion without clear ownership is forbidden.
---
## 7. Systemd and Service Restarts ##
Service restarts are:
- explicit
- grouped
- visible in logs

Deployment scripts must never:
- restart services implicitly
- hide restarts behind file copies
- restart in DRY_RUN mode
---
## 8. Deployment State Recording ##
After a successful deployment:
- the deployed Git commit hash is recorded on the target
- typically under /opt/rollingthunder/.deploy/DEPLOYED_COMMIT
This allows:
- fast correlation between runtime behavior and source state
- postmortem debugging
- confidence that the target matches expectations
---
## 9. Non-Goals ##
The deployment system intentionally avoids:
- configuration management frameworks
- background agents
- push-on-change automation
- hidden synchronization
- “smart” behavior

Deployment should be:
- visible
- intentional
- operator-driven
---
## 10. Change Policy ##
Any change to:
- deployment authority
- managed subtrees
- ownership rules
- DRY_RUN semantics
- deletion behavior

**must** update this document.

If future readers cannot answer:
```
“What exactly does deployment guarantee?”
```
then this document is incomplete.
---
End of Deployment Model