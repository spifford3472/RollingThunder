# RollingThunder Pages & Panels #

### Authoritative Reference ###

This document defines how RollingThunder’s UI is described using declarative JSON
in `config/pages/` and `config/panels/`.

It exists to prevent “UI drift” and accidental coupling between display logic,
controller logic, and service behavior.

This document describes **configuration meaning**, not implementation.

## 1. Purpose ##

RollingThunder’s UI is not a single HTML page or monolithic app.

It is a declarative system where:

- Panels define what data to display and what intents can be emitted
- Pages define which panels are visible and which services must be running

The controller uses configuration to determine service lifecycles.
The display uses configuration to determine layout and rendering.

## 2. File Locations ##

UI configuration is stored under:
```
{
config/
├── pages/
│   └── *.json
└── panels/
    └── *.json
}
```

### Naming conventions (recommended) ###
- Panel file name == panel ID
Example: `config/panels/topbar_core.json contains "id": "topbar_core"`
- Page file name == page ID
Example: `config/pages/hf.json contains "id": "hf"`

## 3. Panel Model (`config/panels/*.json`) ##

Panels are reusable UI components.

### 3.1 Panel fields ###

Required:
- `id` (string, stable, unique)
- `type` (string renderer identifier)
- `focusable` (boolean)
- `bindings` (object describing data sources)

Recommended:
- `refresh` (poll/push behavior)
- `actions` (list of intents the panel can emit)
- `drivingMode` (allowed actions while driving)
- `meta` (human notes; ignored by runtime)

### 3.2 Bindings ###

Bindings specify where the panel reads data.

Binding object shape:

- `source`: `"state"` | `"api"` | `"bus"`
- plus source-specific locator fields:
   - `key` for state (Redis)
   - `url` for API
   - `topic` for bus

Example (state binding):
```
{
"bindings": {
  "snapshot": { "source": "state", "key": "rt:hf:snapshot" }
}
}
```

### 3.3 Actions and intents ###

Panels may define `actions`, but actions are **intents only.**

Panels do not implement behavior.

Example:
```
{
"actions": [
  { "intent": "alert.ack", "params": { "scope": "focused" } }
]
}
```

### 3.4 Driving-mode restrictions ###

Panels may restrict what actions are allowed while driving.

Example:
```
{
"drivingMode": { "allowActions": ["alert.ack"] }
} 
```

Driving-mode rules must be honored regardless of input source
(ESP32, UI button, Meshtastic).

### 3.5 Panel invariants (non-negotiable) ###
- Panels contain **no business logic**
- Panels never directly start/stop services
- Panels never define page navigation
- Panel IDs are stable and never reused

## 4. Page Model (`config/pages/*.json`) ##

Pages are compositions of panels plus lifecycle requirements.

### 4.1 Page fields ###

Required:
- `id` (string, stable, unique)
- `order` (integer; used for page navigation)
- `title` (string; display label)
- `layout` (top/middle/bottom panel composition)
- `requires` (list of service IDs)
- `optional` (list of service IDs)
- `controls.allowedIntents` (list of intents allowed on this page)
- `focusPolicy` (default focus + rotation order)

Recommended:
- meta (human notes)

### 4.2 Layout ###

Layout has three regions:
- `top`: array of panel IDs (typically fixed)
- `middle`: array of columns, each column is an array of panel IDs
- `bottom`: array of panel IDs (typically fixed)

Example:
```
{
"layout": {
  "top": ["topbar_core"],
  "middle": [
    ["test"],
    ["node_health_summary"]
  ],
  "bottom": ["alerts_overlay"]
}
}
```
Interpretation:
- Two middle columns
- One panel in each column
- Top has one panel
- Bottom empty

### 4.3 Service requirements ###

Pages declare what services are needed.
- `requires`: must be running for the page to be considered healthy
- `optional`: may be started to enhance the page but do not block rendering

Service IDs must exist in `config/app.json`.

### 4.4 Controls ###

Pages restrict what intents are allowed while the page is active.

This prevents unexpected behavior and supports driving safety.

Example:
```
{
"controls": {
  "allowedIntents": ["ui.page.next", "ui.page.prev", "alert.ack"]
}
} 
```

### 4.5 Focus policy ###

Pages define:
- the default focused panel
- the focus rotation order

Example:
```
{
"focusPolicy": {
  "defaultPanel": "hf_status",
  "rotation": ["hf_status", "alerts_overlay"]
}
```
}

### 4.6 Page invariants (non-negotiable) ###
- Pages contain no rendering logic
- Pages do not define service implementations
- Pages may only reference known panels and known services
- Page IDs are stable and never reused

### 5. Cross-file invariants and validation rules ###

All of the following must be true:
1. Every panel referenced by any page exists in `config/panels/`
2. Every service referenced by any page exists in `config/app.json`
3. order is unique per page and is an integer
4. layout.middle is 1–3 columns (recommended maximum)
5. Unknown fields are ignored (forward compatibility)

If a change violates these rules, the change is incomplete.

### 6. How to add a new page (the promised workflow) ##

To add a page without touching code:
1. Create a new panel file under `config/panels/` (optional)
2. Create a new page file under `config/pages/`
3. Reference existing panel IDs in the page layout
4. List required/optional services by service ID
5. Commit

No controller logic changes should be required.
---
**End of PAGES_AND_PANELS.md Document**
