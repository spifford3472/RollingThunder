# RollingThunder — Deployment Cookbook #

This document is a **command reference only.**
All rules, ownership boundaries, and guarantees are defined in:
```
docs/DEPLOYMENT_MODEL.md
```

If behavior differs from the model, the model wins.
---
## Common Conventions ##
- Deployment is performed from the **dev machine**
- Default SSH user: `spiff`
- Targets are appliance nodes (`rt-controller`, `rt-display`)
- All scripts support DRY_RUN=1

## Dry Run (Always Recommended First) ##
Shows exactly what would change, without modifying the target.
```
DRY_RUN=1 deploy/push_rt_controller.sh
DRY_RUN=1 deploy/push_rt_display.sh
```

Use this to:
- verify changed files are detected
- confirm no unexpected deletions
- visualize deployment drift
---
## Deploy rt-controller ##
```
deploy/push_rt_controller.sh
```

Dry run:
```
DRY_RUN=1 deploy/push_rt_controller.sh
```

Deploy to a non-default host:
```
deploy/push_rt_controller.sh rt-controller
```
---
## Deploy rt-display ##
```
deploy/push_rt_display.sh
```

Dry run:
```
DRY_RUN=1 deploy/push_rt_display.sh
```

Deploy to a non-default host:
```
deploy/push_rt_display.sh rt-display
```
---
## Verify Deployment State on Target ##
Check deployed commit hash:
```
ssh spiff@<node> "cat /opt/rollingthunder/.deploy/DEPLOYED_COMMIT"
```
---
## Common Troubleshooting ##
**Service didn’t update**
1. Run `DRY_RUN=1` and confirm the file appears in rsync output
2. Verify correct ownership path (`/opt/rollingthunder/services` vs `/nodes`)
3. Restart the affected service explicitly if needed

**systemd unit changes not taking effect**
```
ssh spiff@<node> "sudo systemctl daemon-reload"
```
---
## Never Do This ##

❌ SCP directly into root-owned paths
❌ Edit `/etc/systemd/system/*.service` without redeploying
❌ Overwrite `/etc/rollingthunder/*.env` via deploy scripts

If unsure, stop and read `DEPLOYMENT_MODEL.md`.
---
## Git Hooks (Recommended) ##

RollingThunder includes a `.githooks/` directory containing **optional but strongly recommended** Git hooks. These hooks act as early warning systems to catch mistakes **before** code is committed or pushed, but they are **not** relied upon for correctness or safety.

All hard guarantees are enforced by:
- `tools/rt_verify_repo.sh`
- deploy scripts (`deploy/push_rt_*.sh`)

Hooks exist only to surface problems earlier in the workflow.

### What the hooks do ###

Depending on the hook:
- Run repository invariant checks
- Catch obvious structural mistakes early
- Prevent accidental commits that violate project conventions

Hooks **do not**:
- Modify files automatically
- Deploy code
- Replace deploy-time invariant enforcement

### Enabling the hooks (one-time setup) ###

Git does **not** enable project-local hooks by default. To activate the RollingThunder hooks, run this once from the repo root:
`
git config core.hooksPath .githooks
`
You can verify the setting with:
`
git config --get core.hooksPath
`
Expected output:
`
.githooks
`
### Disabling hooks ###

To disable the project hooks at any time:
`
git config --unset core.hooksPath
`
### Important notes ###
- Hooks are local to your clone and are not enforced by Git itself.
- Deploy scripts always re-run invariant checks regardless of hooks or DRY_RUN.
- Hooks are a convenience and safety net, not a security boundary.

### Rationale ###

RollingThunder intentionally enforces correctness in **deploy-time tooling**, not Git hooks. This ensures:
- CI, automation, and manual deploys all behave consistently
- No hidden dependencies on local developer configuration
- Deterministic, repeatable deployments

If hooks are enabled, you get faster feedback.
If they are not, nothing breaks — deploys will still block on violations.
---
End of Deployment Cookbook
---
**End of DEPLOYMENT.md Document**
