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
End of Deployment Cookbook
---
**End of DEPLOYMENT.md Document**
