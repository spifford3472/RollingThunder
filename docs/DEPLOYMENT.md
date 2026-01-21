# RollingThunder — Deployment Model (Authoritative) #

This document defines ***how code is deployed*** to RollingThunder nodes and ***why***
certain files are owned by ```root``` versus the normal operating user (```spiff```).

It exists to prevent:
- accidental overwrites
- privilege confusion
- brittle manual deployment
- architectural drift between repo layout and runtime layout

If behavior changes but this document is not updated, the change is considered incomplete.

## 1. Deployment Philosophy ##
RollingThunder is an ***appliance-style system***, not a general-purpose workstation.

That means:
- always-on services must be protected from accidental modification
- boot behavior must be deterministic
- deployment steps must be repeatable and explicit

Convenience is secondary to ***stability and safety***.

---
## 2. Ownership Model (Non-Negotiable) ##

RollingThunder uses a ***two-tier ownership model**:

### 2.1 Root-Owned (Protected) ###

Files that:
- start automatically
- run unattended
- affect system-wide behavior
***Must be owned by*** root.

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
- ui_snapshot_api.py
- service_state_publisher.py
- node_presence_ingestor.py (when promoted to always-on)
- future NOAA, Meshtastic, watchdog services

Reason:
- prevents accidental edits
- avoids partial writes during deploy
- stabilizes unattended operation

### 2.2 User-Owned (spiff) ###

Files that:
- are iterated on frequently
- do not define boot behavior directly
- are safe to edit live

***Should be owned by*** spiff.

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
- breakage is visible and recoverable
---
## 3. Repository Layout vs Runtime Layout ##
### 3.1 Repository (Logical Ownership) ###

The repo is organized by ***node and responsibility***:

```
nodes/
├── rt-controller/
│   ├── services/
│   │   └── ui_snapshot_api.py
│   ├── systemd/
│   └── ...
```

This expresses ***ownership and intent***, not execution context.

## 3.2 Runtime (Operational Simplicity) ##

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
## 4. Deployment Rules (Authoritative) ##
### Rule 1 — Never SCP directly into root-owned paths ###

This is ***not allowed***:
```
scp file.py user@host:/opt/rollingthunder/services/file.py   ❌
```

Reason:
- bypasses ownership boundary
- risks partial writes
- defeats appliance protections

### Rule 2 — Root-owned files must be staged via /tmp ###

Correct pattern:
```
scp file.py user@host:/tmp/file.py
ssh user@host "sudo mv /tmp/file.py /opt/rollingthunder/services/file.py"
ssh user@host "sudo chown root:root /opt/rollingthunder/services/file.py"
ssh user@host "sudo chmod 755 /opt/rollingthunder/services/file.py"
```

This ensures:
- atomic replacement
- correct ownership
- explicit privilege escalation

### Rule 3 — systemd must always be reloaded after unit changes ###

After modifying /etc/systemd/system/*.service:
```
sudo systemctl daemon-reload
sudo systemctl restart <service>
```

Skipping daemon-reload is considered a deployment error.
---

## SSH Key-Based Deployment (Recommended)

RollingThunder deployment should be performed using a dedicated SSH key from the dev machine
to avoid password prompts and to support deterministic scripts.

### Create a dedicated deploy key (dev machine)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_rt_deploy -C "rollingthunder-deploy"
ssh-copy-id -i ~/.ssh/id_ed25519_rt_deploy.pub spiff@rt-controller

## 5. Deployment Scripts (Preferred Mechanism) ##

Manual deployment is acceptable during early development,
but ***deployment scripts are the preferred and future-proof mechanism***.

Scripts should:
- encode ownership rules
- handle /tmp staging automatically
- restart only affected services
- optionally perform smoke checks

Example scripts:
```
deploy/push_rt_controller.sh
deploy/push_rt_display.sh
```

These scripts serve as:
- automation
- documentation
- enforcement of architectural intent
---
## 6. Security & Reliability Rationale ##

This model ensures:
- accidental shell mistakes cannot rewrite boot services
- compromised user account cannot silently alter appliance behavior
- services behave the same after reboot as before
- future contributors understand why boundaries exist

This is not overengineering.
This is how ***reliable systems stay reliable***.
---
## 7. When This Document Must Be Updated ##

Update this document when:
- a new always-on service is added
- ownership rules change
- runtime layout changes
- deployment process changes materially

If a future reader cannot answer:
   “Why is this file owned by root?”
then the documentation is incomplete.
---
***End of Deployment Model***