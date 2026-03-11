// nav_machine.js
//
// Drop-in replacement for the existing nav_machine.js.
// Preserves existing API:
//   - setPageModel
//   - panelNext
//   - panelPrev
//   - clearFocus
//   - getState
//
// Adds minimal intentional navigation model support:
//   - explicit NAV_STATE (GLOBAL_FOCUS / PANEL_BROWSE / MODAL_DIALOG)
//   - input ownership (getInputOwner) for deterministic routing
//   - explicit transitions for OK/CANCEL flows (beginBrowse/endBrowse/openModal/closeModal)
//   - optional per-page "remember last focus" behavior
//   - absolute focus setter: setActivePanel(panelId)
//
// This file does not emit intents, write Redis, or implement panel logic.
// It only manages focus + navigation state + capture.

function uniq(arr) {
  const out = [];
  const seen = new Set();
  for (const x of arr) {
    const s = String(x || "").trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

export const NAV_STATE = Object.freeze({
  GLOBAL_FOCUS: "GLOBAL_FOCUS",
  PANEL_BROWSE: "PANEL_BROWSE",
  MODAL_DIALOG: "MODAL_DIALOG",
});

export function createNavMachine() {
  // Focus model (existing behavior)
  let _order = [];
  let _slotById = new Map();
  let _idx = -1;
  let _activeId = null;

  // New: nav state + capture
  let _state = NAV_STATE.GLOBAL_FOCUS;
  // When captured: { kind: "panel"|"modal", id: string, prevState: NAV_STATE.* }
  let _capture = null;

  // New: optional per-page focus memory
  let _pageId = null;
  const _lastFocusByPage = new Map();

  function _clearActiveClass() {
    if (_activeId) {
      const el = _slotById.get(_activeId);
      if (el) el.classList.remove("rt-active");
    }
  }

  function _setActive(panelId) {
    _clearActiveClass();
    _activeId = panelId || null;
    if (_activeId) {
      const el = _slotById.get(_activeId);
      if (el) el.classList.add("rt-active");
    }
  }

  /**
   * setPageModel({
   *   pageId,                 // optional stable page id (enables rememberFocus)
   *   focusablePanelIds,      // already computed in desired cycle order
   *   slotByPanelId,          // Map(panelId -> DOM element)
   *   initialPanelId = null,  // focus this panel if present in order
   *   rememberFocus = true    // restore last focused panel for this page
   * })
   */
  function setPageModel({
    pageId = null,
    focusablePanelIds,
    slotByPanelId,
    initialPanelId = null,
    rememberFocus = true,
  }) {
    _pageId = pageId ? String(pageId) : null;

    _order = uniq(focusablePanelIds || []);
    _slotById = slotByPanelId || new Map();

    // Page change resets capture/state deterministically.
    _state = NAV_STATE.GLOBAL_FOCUS;
    _capture = null;

    // Optional: restore last focus for this page
    if (!initialPanelId && rememberFocus && _pageId) {
      const last = _lastFocusByPage.get(_pageId);
      if (last && _order.includes(last)) initialPanelId = last;
    }

    if (initialPanelId && _order.includes(initialPanelId)) {
      _idx = _order.indexOf(initialPanelId);
      _setActive(initialPanelId);
      if (_pageId) _lastFocusByPage.set(_pageId, initialPanelId);
      return;
    }

    // Start with NO focus if no initial panel is specified/valid
    _idx = -1;
    _setActive(null);
    if (_pageId) _lastFocusByPage.delete(_pageId);
  }

  function panelNext() {
    // Focus cycling only in GLOBAL_FOCUS
    if (_state !== NAV_STATE.GLOBAL_FOCUS) return;
    if (_order.length === 0) return;

    if (_idx < 0) _idx = 0;
    else _idx = (_idx + 1) % _order.length;

    _setActive(_order[_idx]);
    if (_pageId && _activeId) _lastFocusByPage.set(_pageId, _activeId);
  }

  function panelPrev() {
    // Focus cycling only in GLOBAL_FOCUS
    if (_state !== NAV_STATE.GLOBAL_FOCUS) return;
    if (_order.length === 0) return;

    if (_idx < 0) _idx = _order.length - 1;
    else _idx = (_idx - 1 + _order.length) % _order.length;

    _setActive(_order[_idx]);
    if (_pageId && _activeId) _lastFocusByPage.set(_pageId, _activeId);
  }

  function clearFocus() {
    // Only meaningful in GLOBAL_FOCUS
    if (_state !== NAV_STATE.GLOBAL_FOCUS) return;
    _idx = -1;
    _setActive(null);
    if (_pageId) _lastFocusByPage.delete(_pageId);
  }

  // New: absolute focus setter
  function setActivePanel(panelId) {
    if (_state !== NAV_STATE.GLOBAL_FOCUS) return false;

    const id = String(panelId || "").trim();
    if (!id) return false;
    if (!_order.includes(id)) return false;
    if (!_slotById.has(id)) return false;

    _idx = _order.indexOf(id);
    if (_idx < 0) return false;

    _setActive(id);
    if (_pageId && _activeId) _lastFocusByPage.set(_pageId, _activeId);
    return true;
  }

  // ---- New: explicit transitions ----

  // Enter panel browse (panel captures input)
  function beginBrowse() {
    if (_state !== NAV_STATE.GLOBAL_FOCUS) return false;
    if (!_activeId) return false;
    _state = NAV_STATE.PANEL_BROWSE;
    _capture = { kind: "panel", id: _activeId, prevState: NAV_STATE.GLOBAL_FOCUS };
    return true;
  }

  // Exit browse (return to global focus)
  function endBrowse() {
    if (_state !== NAV_STATE.PANEL_BROWSE) return false;
    _state = NAV_STATE.GLOBAL_FOCUS;
    _capture = null;
    return true;
  }

  // Open modal (modal captures input)
  function openModal(modalId = "modal") {
    if (_state === NAV_STATE.MODAL_DIALOG) return false;
    const prev = _state;
    _state = NAV_STATE.MODAL_DIALOG;
    _capture = { kind: "modal", id: String(modalId || "modal"), prevState: prev };
    return true;
  }

  // Close modal and restore prior state
  function closeModal() {
    if (_state !== NAV_STATE.MODAL_DIALOG) return false;
    const prev = _capture?.prevState || NAV_STATE.GLOBAL_FOCUS;
    _state = prev;
    _capture = null;
    return true;
  }

  // Who owns input right now? (for your key routing layer)
  function getInputOwner() {
    if (_state === NAV_STATE.MODAL_DIALOG) {
      return { owner: "modal", id: _capture?.id || "modal", state: _state };
    }
    if (_state === NAV_STATE.PANEL_BROWSE) {
      return { owner: "panel", id: _capture?.id || _activeId, state: _state };
    }
    return { owner: "nav", id: _activeId, state: _state };
  }

  function getState() {
    return {
      state: _state,
      pageId: _pageId,
      activePanelId: _activeId,
      hasFocus: _idx >= 0,
      focusableCount: _order.length,
      capture: _capture,
    };
  }

  return {
    // existing
    setPageModel,
    panelNext,
    panelPrev,
    clearFocus,
    getState,

    // new
    setActivePanel,
    beginBrowse,
    endBrowse,
    openModal,
    closeModal,
    getInputOwner,
  };
}