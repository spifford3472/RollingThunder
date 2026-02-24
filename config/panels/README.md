Panels may declare interaction capability in their panel JSON via an optional input block:

```
"input": {
  "mode": "none" | "browse" | "modal",
  "previewOnSelection": false,
  "intents": {
    "browseDelta": "ui.browse.delta",
    "select": "ui.browse.select",
    "ok": "ui.ok",
    "cancel": "ui.cancel"
  }
}
```
**Rules:**
- If absent, the panel is assumed "mode": "none" (focus highlight only).
- `mode: "browse"` means the panel can accept rotary delta + enter to open modal.
- `mode: "modal"` is reserved for panels that are always modal when entered (rare).
- Preview-on-selection MUST be implemented as controller-mediated effects, not UI-side direct control.

This keeps panel interaction declarative and prevents runtime.js from accumulating panel-specific hacks.