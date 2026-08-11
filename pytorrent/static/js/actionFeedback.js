(() => {
  'use strict';

  if (window.pyTorrentActionFeedback) return;

  const buttonStates = new WeakMap();
  const nativeFetch = typeof window.fetch === 'function' ? window.fetch.bind(window) : null;
  const minimumLoadingMs = 110;
  const successVisibleMs = 380;
  const errorVisibleMs = 600;
  let clickContextButton = null;
  let clickContextTimer = null;
  let continuationButton = null;
  let continuationTimer = null;
  let manualContextButton = null;

  // Note: Registering Bootstrap buttons once keeps click feedback consistent for static and dynamically rendered actions.
  function registerButton(button) {
    if (!(button instanceof HTMLElement) || !button.classList.contains('btn') || button.classList.contains('btn-close')) return;
    button.classList.add('action-feedback-button');
  }

  // Note: Dynamic tables and modal content can create action buttons after startup, so new DOM branches are registered automatically.
  function registerButtons(root = document) {
    if (root instanceof HTMLElement && root.classList.contains('btn')) registerButton(root);
    root.querySelectorAll?.('.btn').forEach(registerButton);
  }

  // Note: Bootstrap navigation and dismiss controls keep their native behavior and never enter asynchronous action states.
  function isRequestActionButton(button) {
    return !!(
      button instanceof HTMLElement
      && button.classList.contains('btn')
      && !button.classList.contains('btn-close')
      && button.dataset.actionFeedback !== 'off'
      && !button.hasAttribute('data-bs-dismiss')
      && !button.hasAttribute('data-bs-toggle')
    );
  }

  // Note: Action state is reserved before application handlers run so local busy helpers and network feedback share one lifecycle.
  function createButtonState(button) {
    const previous = buttonStates.get(button);
    if (previous?.pending > 0) return previous;
    if (previous) restoreButton(button, previous);

    const state = {
      originalAriaBusy: button.getAttribute('aria-busy'),
      originalAriaLabel: button.getAttribute('aria-label'),
      replacedIcon: null,
      pending: 0,
      manualPending: 0,
      networkSeen: false,
      failed: false,
      startedAt: 0,
      terminalTimer: null,
      navigationTimer: null,
    };
    buttonStates.set(button, state);
    return state;
  }

  // Note: Only the leading action icon is swapped, keeping button text and dimensions stable during loading and result feedback.
  function renderIndicator(button, state, phase) {
    const currentIndicator = button.querySelector('[data-action-feedback-indicator="1"]');
    let indicator;

    if (phase === 'loading') {
      indicator = document.createElement('span');
      indicator.className = 'spinner-border spinner-border-sm action-feedback-indicator';
      indicator.setAttribute('role', 'status');
    } else {
      indicator = document.createElement('i');
      indicator.className = `fa-solid ${phase === 'success' ? 'fa-check' : 'fa-triangle-exclamation'} action-feedback-indicator`;
      indicator.setAttribute('aria-hidden', 'true');
    }
    indicator.dataset.actionFeedbackIndicator = '1';

    if (currentIndicator) {
      currentIndicator.replaceWith(indicator);
      return;
    }

    const leadingIcon = button.querySelector('i, svg, .spinner-border');
    if (leadingIcon) {
      state.replacedIcon = leadingIcon;
      leadingIcon.replaceWith(indicator);
      return;
    }

    indicator.classList.add('action-feedback-indicator-floating');
    button.appendChild(indicator);
  }

  // Note: Loading state blocks duplicate clicks through UI state without changing application-owned disabled rules.
  function beginButtonRequest(button, source = 'network') {
    if (!isRequestActionButton(button)) return null;
    const state = createButtonState(button);

    if (state.pending === 0) {
      clearTimeout(state.terminalTimer);
      clearTimeout(state.navigationTimer);
      state.failed = false;
      state.networkSeen = false;
      state.startedAt = performance.now();
      button.classList.remove('is-action-success', 'is-action-error');
      button.classList.add('is-action-loading');
      button.setAttribute('aria-busy', 'true');
      renderIndicator(button, state, 'loading');
    }

    state.pending += 1;
    if (source === 'manual') state.manualPending += 1;
    else state.networkSeen = true;
    return state;
  }

  // Note: The terminal pulse stays visible long enough to read while keeping fast actions responsive.
  function finishButtonRequest(button, succeeded, source = 'network') {
    const state = buttonStates.get(button);
    if (!state) return;

    if (source === 'manual') state.manualPending = Math.max(0, state.manualPending - 1);
    else {
      state.networkSeen = true;
      state.failed = state.failed || !succeeded;
    }
    state.pending = Math.max(0, state.pending - 1);
    if (state.pending > 0) return;
    if (!state.networkSeen) {
      restoreButton(button, state);
      return;
    }

    const elapsed = Math.max(0, performance.now() - state.startedAt);
    const delay = Math.max(0, minimumLoadingMs - elapsed);
    clearTimeout(state.terminalTimer);
    state.terminalTimer = window.setTimeout(() => {
      if (state.pending > 0) return;
      const phase = state.failed ? 'error' : 'success';
      button.classList.remove('is-action-loading');
      button.classList.add(phase === 'success' ? 'is-action-success' : 'is-action-error');
      button.setAttribute('aria-busy', 'false');
      renderIndicator(button, state, phase);
      state.terminalTimer = window.setTimeout(
        () => restoreButton(button, state),
        phase === 'success' ? successVisibleMs : errorVisibleMs,
      );
    }, delay);
  }

  // Note: Restoring from the pre-click snapshot leaves existing handlers free to manage labels, disabled state and Bootstrap classes as before.
  function restoreButton(button, state = buttonStates.get(button)) {
    if (!state) return;
    clearTimeout(state.terminalTimer);
    clearTimeout(state.navigationTimer);
    button.classList.remove('is-action-loading', 'is-action-success', 'is-action-error');
    const indicator = button.querySelector('[data-action-feedback-indicator="1"]');
    if (indicator && state.replacedIcon) indicator.replaceWith(state.replacedIcon);
    else indicator?.remove();
    if (state.originalAriaBusy === null) button.removeAttribute('aria-busy');
    else button.setAttribute('aria-busy', state.originalAriaBusy);
    if (state.originalAriaLabel === null) button.removeAttribute('aria-label');
    else button.setAttribute('aria-label', state.originalAriaLabel);
    buttonStates.delete(button);
  }

  // Note: JSON APIs can report a logical failure with HTTP 200, so the visual result also checks a returned ok:false flag.
  async function responseSucceeded(response) {
    if (!response?.ok) return false;
    const contentType = String(response.headers?.get?.('content-type') || '').toLowerCase();
    if (!contentType.includes('json')) return true;
    try {
      const payload = await response.clone().json();
      return !(payload && typeof payload === 'object' && payload.ok === false);
    } catch (_) {
      return false;
    }
  }

  // Note: A zero-delay click context links synchronous fetch calls from the current action without tagging unrelated background polling requests.
  function setClickContext(button) {
    clickContextButton = button;
    clearTimeout(clickContextTimer);
    clickContextTimer = window.setTimeout(() => {
      if (clickContextButton === button) clickContextButton = null;
      const state = buttonStates.get(button);
      if (state && state.pending === 0) buttonStates.delete(button);
    }, 0);
  }

  // Note: A one-turn continuation also covers immediate follow-up requests started after awaiting the first action response.
  function setContinuationContext(button) {
    continuationButton = button;
    clearTimeout(continuationTimer);
    continuationTimer = window.setTimeout(() => {
      if (continuationButton === button) continuationButton = null;
    }, 0);
  }

  // Note: Legacy buttonBusy() calls can hold the shared loading state across validation, file preparation and multiple sequential requests.
  function setManualBusy(button, active) {
    if (!isRequestActionButton(button)) return;
    if (active) {
      beginButtonRequest(button, 'manual');
      manualContextButton = button;
      return;
    }

    const state = buttonStates.get(button);
    if (!state?.manualPending) return;
    finishButtonRequest(button, true, 'manual');
    if (manualContextButton === button && !buttonStates.get(button)?.manualPending) manualContextButton = null;
  }

  // Note: Capturing the click before application listeners preserves the exact original icon even when local busy helpers run later.
  document.addEventListener('click', (event) => {
    const button = event.target?.closest?.('.btn');
    if (!isRequestActionButton(button) || button.disabled || button.getAttribute('aria-disabled') === 'true') return;
    if (button.classList.contains('is-action-loading')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    createButtonState(button);
    setClickContext(button);
  }, true);

  // Note: Native form submits get the same loading treatment until navigation replaces the page, including the standalone login form.
  document.addEventListener('submit', (event) => {
    const button = event.submitter;
    if (!isRequestActionButton(button) || button.disabled) return;
    const state = beginButtonRequest(button);
    if (!state) return;
    state.navigationTimer = window.setTimeout(() => restoreButton(button, state), 10000);
  }, true);

  if (nativeFetch) {
    // Note: Wrapping fetch globally provides action feedback to existing Save/Apply/Check/Delete handlers without modifying their business logic.
    window.fetch = async (...args) => {
      const manualState = buttonStates.get(manualContextButton);
      const button = isRequestActionButton(clickContextButton)
        ? clickContextButton
        : (isRequestActionButton(manualContextButton) && manualState?.manualPending > 0
          ? manualContextButton
          : (isRequestActionButton(continuationButton) ? continuationButton : null));
      if (button) beginButtonRequest(button, 'network');

      try {
        const response = await nativeFetch(...args);
        if (button) {
          const succeeded = await responseSucceeded(response);
          finishButtonRequest(button, succeeded);
          setContinuationContext(button);
        }
        return response;
      } catch (error) {
        if (button) {
          finishButtonRequest(button, false);
          setContinuationContext(button);
        }
        throw error;
      }
    };
  }

  registerButtons();
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node instanceof HTMLElement) registerButtons(node);
    }));
  });
  observer.observe(document.documentElement, {childList: true, subtree: true});

  window.pyTorrentActionFeedback = {registerButtons, setManualBusy};
})();
