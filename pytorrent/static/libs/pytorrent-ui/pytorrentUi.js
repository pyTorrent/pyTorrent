/*
 * pyTorrent UI runtime
 *
 * Lightweight Bootstrap-compatible behavior used only when the pyTorrent CSS
 * framework is active. It implements the subset used by the application:
 * modals, dropdowns, pill/tab navigation and tooltips.
 */
(() => {
  'use strict';

  if (document.documentElement.dataset.uiFramework !== 'pytorrent') return;

  const modalInstances = new WeakMap();
  const dropdownInstances = new WeakMap();
  const tooltipInstances = new WeakMap();
  const openDropdowns = new Set();
  const openModals = [];
  function transitionMs() {
    if (document.documentElement.dataset.pytorrentMotion === 'off') return 0;
    const value = getComputedStyle(document.documentElement).getPropertyValue('--pt-motion-base').trim();
    if (!value) return 180;
    const number = Number.parseFloat(value);
    if (!Number.isFinite(number)) return 180;
    return value.endsWith('s') && !value.endsWith('ms') ? number * 1000 : number;
  }

  function resolveElement(value) {
    if (!value) return null;
    if (value instanceof Element) return value;
    if (typeof value !== 'string') return null;
    try { return document.querySelector(value); } catch (_) { return null; }
  }

  function selectorFromTrigger(trigger) {
    const explicit = trigger?.getAttribute('data-bs-target');
    if (explicit && explicit !== '#') return explicit.trim();
    const href = trigger?.getAttribute('href');
    return href && href.includes('#') ? href.slice(href.indexOf('#')) : null;
  }

  function eventWithRelatedTarget(name, relatedTarget = null, cancelable = true) {
    const event = new Event(name, { bubbles: true, cancelable });
    Object.defineProperty(event, 'relatedTarget', { configurable: true, value: relatedTarget });
    return event;
  }

  function emit(element, name, relatedTarget = null, cancelable = true) {
    const event = eventWithRelatedTarget(name, relatedTarget, cancelable);
    element.dispatchEvent(event);
    return event;
  }

  function focusableElements(root) {
    return [...root.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )].filter((element) => !element.hidden && element.getClientRects().length > 0);
  }

  function topModal() {
    return openModals.length ? openModals[openModals.length - 1] : null;
  }

  class Modal {
    constructor(element, options = {}) {
      element = resolveElement(element);
      if (!element) throw new TypeError('Modal target is required');
      const existing = modalInstances.get(element);
      if (existing) return existing;
      this._element = element;
      this._options = {
        backdrop: element.dataset.bsBackdrop === 'static' ? 'static' : options.backdrop !== false,
        keyboard: options.keyboard !== false && element.dataset.bsKeyboard !== 'false',
        focus: options.focus !== false,
      };
      this._backdrop = null;
      this._shown = element.classList.contains('show');
      this._trigger = null;
      this._onModalClick = (event) => {
        if (event.target !== this._element) return;
        if (this._options.backdrop === true) this.hide();
        else if (this._options.backdrop === 'static') this._element.focus({ preventScroll: true });
      };
      this._element.addEventListener('mousedown', this._onModalClick);
      modalInstances.set(element, this);
    }

    static getInstance(element) {
      return modalInstances.get(resolveElement(element)) || null;
    }

    static getOrCreateInstance(element, options = {}) {
      return Modal.getInstance(element) || new Modal(element, options);
    }

    show(relatedTarget = null) {
      if (this._shown) return;
      const showEvent = emit(this._element, 'show.bs.modal', relatedTarget, true);
      if (showEvent.defaultPrevented) return;

      this._shown = true;
      this._trigger = relatedTarget instanceof Element ? relatedTarget : document.activeElement;
      openModals.push(this);
      document.body.classList.add('modal-open');

      if (this._options.backdrop) {
        const backdrop = document.createElement('div');
        backdrop.className = 'modal-backdrop fade';
        backdrop.setAttribute('aria-hidden', 'true');
        this._backdrop = backdrop;
        this._element.insertAdjacentElement('afterend', backdrop);
        requestAnimationFrame(() => backdrop.classList.add('show'));
      }

      this._element.style.display = 'block';
      this._element.removeAttribute('aria-hidden');
      this._element.setAttribute('aria-modal', 'true');
      if (!this._element.hasAttribute('role')) this._element.setAttribute('role', 'dialog');
      this._element.scrollTop = 0;
      void this._element.offsetWidth;
      this._element.classList.add('show');

      window.setTimeout(() => {
        if (!this._shown) return;
        if (this._options.focus) {
          const focusTarget = this._element.querySelector('[autofocus]') || this._element;
          if (!this._element.hasAttribute('tabindex')) this._element.tabIndex = -1;
          focusTarget.focus?.({ preventScroll: true });
        }
        emit(this._element, 'shown.bs.modal', relatedTarget, false);
      }, transitionMs());
    }

    hide() {
      if (!this._shown) return;
      const hideEvent = emit(this._element, 'hide.bs.modal', null, true);
      if (hideEvent.defaultPrevented) return;

      this._shown = false;
      this._element.classList.remove('show');
      this._backdrop?.classList.remove('show');

      window.setTimeout(() => {
        this._element.style.display = 'none';
        this._element.setAttribute('aria-hidden', 'true');
        this._element.removeAttribute('aria-modal');
        this._backdrop?.remove();
        this._backdrop = null;

        const index = openModals.lastIndexOf(this);
        if (index >= 0) openModals.splice(index, 1);
        if (!openModals.length) document.body.classList.remove('modal-open');

        emit(this._element, 'hidden.bs.modal', null, false);
        const nextModal = topModal();
        if (nextModal) nextModal._element.focus?.({ preventScroll: true });
        else if (this._trigger?.isConnected) this._trigger.focus?.({ preventScroll: true });
      }, transitionMs());
    }

    toggle(relatedTarget = null) {
      this._shown ? this.hide() : this.show(relatedTarget);
    }

    dispose() {
      if (this._shown) this.hide();
      this._element.removeEventListener('mousedown', this._onModalClick);
      modalInstances.delete(this._element);
    }
  }

  class Dropdown {
    constructor(toggle) {
      toggle = resolveElement(toggle);
      if (!toggle) throw new TypeError('Dropdown toggle is required');
      const existing = dropdownInstances.get(toggle);
      if (existing) return existing;
      this._toggle = toggle;
      this._parent = toggle.closest('.dropdown') || toggle.parentElement;
      this._menu = this._parent?.querySelector('.dropdown-menu') || null;
      dropdownInstances.set(toggle, this);
    }

    static getInstance(element) {
      return dropdownInstances.get(resolveElement(element)) || null;
    }

    static getOrCreateInstance(element) {
      return Dropdown.getInstance(element) || new Dropdown(element);
    }

    _autoClose() {
      const value = String(this._toggle.dataset.bsAutoClose || 'true').toLowerCase();
      if (value === 'false') return false;
      if (value === 'inside' || value === 'outside') return value;
      return true;
    }

    _fitToViewport() {
      if (!this._menu) return;
      this._menu.style.removeProperty('left');
      this._menu.style.removeProperty('right');
      this._menu.style.removeProperty('top');
      this._menu.style.removeProperty('bottom');
      const rect = this._menu.getBoundingClientRect();
      const gutter = 8;
      if (rect.right > window.innerWidth - gutter) {
        this._menu.style.left = 'auto';
        this._menu.style.right = '0';
      }
      const fitted = this._menu.getBoundingClientRect();
      if (fitted.left < gutter) {
        this._menu.style.left = '0';
        this._menu.style.right = 'auto';
      }
      const finalRect = this._menu.getBoundingClientRect();
      const parentRect = this._parent?.getBoundingClientRect();
      if (parentRect && finalRect.bottom > window.innerHeight - gutter && parentRect.top > finalRect.height + gutter) {
        this._menu.style.top = 'auto';
        this._menu.style.bottom = 'calc(100% + .34rem)';
      }
    }

    show() {
      if (!this._menu || this._menu.classList.contains('show')) return;
      const event = emit(this._parent || this._toggle, 'show.bs.dropdown', this._toggle, true);
      if (event.defaultPrevented) return;
      for (const dropdown of [...openDropdowns]) {
        if (dropdown !== this) dropdown.hide();
      }
      this._menu.classList.add('show');
      this._toggle.classList.add('show');
      this._toggle.setAttribute('aria-expanded', 'true');
      openDropdowns.add(this);
      this._fitToViewport();
      emit(this._parent || this._toggle, 'shown.bs.dropdown', this._toggle, false);
    }

    hide() {
      if (!this._menu?.classList.contains('show')) return;
      const event = emit(this._parent || this._toggle, 'hide.bs.dropdown', this._toggle, true);
      if (event.defaultPrevented) return;
      this._menu.classList.remove('show');
      this._toggle.classList.remove('show');
      this._toggle.setAttribute('aria-expanded', 'false');
      this._menu.style.removeProperty('left');
      this._menu.style.removeProperty('right');
      this._menu.style.removeProperty('top');
      this._menu.style.removeProperty('bottom');
      openDropdowns.delete(this);
      emit(this._parent || this._toggle, 'hidden.bs.dropdown', this._toggle, false);
    }

    toggle() {
      this._menu?.classList.contains('show') ? this.hide() : this.show();
    }

    items() {
      if (!this._menu) return [];
      return [...this._menu.querySelectorAll('.dropdown-item:not(.disabled):not([disabled]), [role="menuitem"]:not(.disabled):not([disabled])')]
        .filter((item) => item.getClientRects().length > 0);
    }

    focusItem(direction = 1, edge = null) {
      this.show();
      const items = this.items();
      if (!items.length) return;
      if (edge === 'first') { items[0].focus(); return; }
      if (edge === 'last') { items[items.length - 1].focus(); return; }
      const current = items.indexOf(document.activeElement);
      const start = current >= 0 ? current : (direction > 0 ? -1 : 0);
      const next = (start + direction + items.length) % items.length;
      items[next].focus();
    }

    dispose() {
      this.hide();
      dropdownInstances.delete(this._toggle);
    }
  }

  class Tooltip {
    constructor(element, options = {}) {
      element = resolveElement(element);
      if (!element) throw new TypeError('Tooltip target is required');
      const existing = tooltipInstances.get(element);
      if (existing) return existing;
      this._element = element;
      this._placement = options.placement || element.dataset.bsPlacement || 'top';
      this._title = options.title || element.getAttribute('title') || '';
      this._tip = null;
      this._show = () => this.show();
      this._hide = () => this.hide();
      if (this._title) {
        element.dataset.ptOriginalTitle = this._title;
        element.removeAttribute('title');
      }
      element.addEventListener('mouseenter', this._show);
      element.addEventListener('focusin', this._show);
      element.addEventListener('mouseleave', this._hide);
      element.addEventListener('focusout', this._hide);
      tooltipInstances.set(element, this);
    }

    static getInstance(element) {
      return tooltipInstances.get(resolveElement(element)) || null;
    }

    static getOrCreateInstance(element, options = {}) {
      return Tooltip.getInstance(element) || new Tooltip(element, options);
    }

    show() {
      if (!this._title || this._tip) return;
      const tip = document.createElement('div');
      tip.className = `tooltip bs-tooltip-${this._placement} fade`;
      tip.setAttribute('role', 'tooltip');
      tip.dataset.popperPlacement = this._placement;
      const arrow = document.createElement('div');
      arrow.className = 'tooltip-arrow';
      const inner = document.createElement('div');
      inner.className = 'tooltip-inner';
      inner.textContent = this._title;
      tip.append(arrow, inner);
      document.body.appendChild(tip);
      this._tip = tip;

      const triggerRect = this._element.getBoundingClientRect();
      const tipRect = tip.getBoundingClientRect();
      const gap = 7;
      let top = triggerRect.top - tipRect.height - gap;
      let left = triggerRect.left + (triggerRect.width - tipRect.width) / 2;
      if (this._placement === 'bottom') top = triggerRect.bottom + gap;
      left = Math.max(6, Math.min(left, window.innerWidth - tipRect.width - 6));
      if (top < 6) top = triggerRect.bottom + gap;
      tip.style.position = 'fixed';
      tip.style.top = `${Math.round(top)}px`;
      tip.style.left = `${Math.round(left)}px`;
      requestAnimationFrame(() => {
        if (this._tip === tip) tip.classList.add('show');
      });
    }

    hide() {
      const tip = this._tip;
      if (!tip) return;
      this._tip = null;
      if (transitionMs() === 0) {
        tip.remove();
        return;
      }
      tip.classList.remove('show');
      window.setTimeout(() => tip.remove(), transitionMs());
    }

    dispose() {
      this.hide();
      this._element.removeEventListener('mouseenter', this._show);
      this._element.removeEventListener('focusin', this._show);
      this._element.removeEventListener('mouseleave', this._hide);
      this._element.removeEventListener('focusout', this._hide);
      if (this._title) this._element.setAttribute('title', this._title);
      delete this._element.dataset.ptOriginalTitle;
      tooltipInstances.delete(this._element);
    }
  }

  function activateTab(trigger) {
    const selector = selectorFromTrigger(trigger);
    const pane = resolveElement(selector);
    if (!pane) return;
    const tabList = trigger.closest('[role="tablist"], .nav');
    const previous = tabList?.querySelector('[data-bs-toggle="pill"].active, [data-bs-toggle="tab"].active') || null;
    if (previous === trigger) return;
    const previousPane = previous ? resolveElement(selectorFromTrigger(previous)) : null;

    const hideEvent = previous ? emit(previous, 'hide.bs.tab', trigger, true) : null;
    const showEvent = emit(trigger, 'show.bs.tab', previous, true);
    if (hideEvent?.defaultPrevented || showEvent.defaultPrevented) return;

    previous?.classList.remove('active');
    previous?.setAttribute('aria-selected', 'false');
    previous?.setAttribute('tabindex', '-1');
    if (previousPane) previousPane.classList.remove('show', 'active');

    trigger.classList.add('active');
    trigger.setAttribute('aria-selected', 'true');
    trigger.removeAttribute('tabindex');
    pane.classList.add('active');
    requestAnimationFrame(() => pane.classList.add('show'));

    if (previous) emit(previous, 'hidden.bs.tab', trigger, false);
    emit(trigger, 'shown.bs.tab', previous, false);
  }

  document.addEventListener('click', (event) => {
    const modalToggle = event.target.closest('[data-bs-toggle="modal"]');
    if (modalToggle) {
      const target = resolveElement(selectorFromTrigger(modalToggle));
      if (target) {
        if (modalToggle.tagName === 'A') event.preventDefault();
        Modal.getOrCreateInstance(target).show(modalToggle);
      }
      return;
    }

    const dismiss = event.target.closest('[data-bs-dismiss="modal"]');
    if (dismiss) {
      const modal = dismiss.closest('.modal');
      if (modal) {
        event.preventDefault();
        Modal.getOrCreateInstance(modal).hide();
      }
      return;
    }

    const dropdownToggle = event.target.closest('[data-bs-toggle="dropdown"]');
    if (dropdownToggle) {
      event.preventDefault();
      event.stopPropagation();
      Dropdown.getOrCreateInstance(dropdownToggle).toggle();
      return;
    }

    const tabTrigger = event.target.closest('[data-bs-toggle="pill"], [data-bs-toggle="tab"]');
    if (tabTrigger) {
      event.preventDefault();
      activateTab(tabTrigger);
      return;
    }

    for (const dropdown of [...openDropdowns]) {
      const insideParent = dropdown._parent?.contains(event.target);
      const insideMenu = dropdown._menu?.contains(event.target);
      const autoClose = dropdown._autoClose();
      if (autoClose === false) continue;
      if (autoClose === 'outside') {
        if (!insideParent) dropdown.hide();
        continue;
      }
      if (autoClose === 'inside') {
        if (insideMenu) dropdown.hide();
        continue;
      }
      if (!insideParent) {
        dropdown.hide();
        continue;
      }
      const interactive = event.target.closest('input, select, option, textarea, form');
      if (insideMenu && !interactive) dropdown.hide();
    }
  });

  document.addEventListener('keydown', (event) => {
    const dropdownRoot = event.target.closest?.('.dropdown');
    const dropdownToggle = dropdownRoot?.querySelector('[data-bs-toggle="dropdown"]') || event.target.closest?.('[data-bs-toggle="dropdown"]');
    if (dropdownToggle && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      const dropdown = Dropdown.getOrCreateInstance(dropdownToggle);
      if (event.key === 'Home') dropdown.focusItem(1, 'first');
      else if (event.key === 'End') dropdown.focusItem(1, 'last');
      else dropdown.focusItem(event.key === 'ArrowUp' ? -1 : 1);
      event.preventDefault();
      return;
    }

    if (event.key === 'Escape') {
      if (openDropdowns.size) {
        const dropdown = [...openDropdowns].pop();
        dropdown.hide();
        dropdown._toggle.focus?.({ preventScroll: true });
        event.preventDefault();
        return;
      }
      const modal = topModal();
      if (modal?._options.keyboard) {
        modal.hide();
        event.preventDefault();
      }
      return;
    }

    if (event.key !== 'Tab') return;
    const modal = topModal();
    if (!modal || !modal._shown) return;
    const focusables = focusableElements(modal._element);
    if (!focusables.length) {
      event.preventDefault();
      modal._element.focus?.({ preventScroll: true });
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  window.addEventListener('resize', () => {
    for (const dropdown of openDropdowns) dropdown._fitToViewport();
  }, { passive: true });

  window.pyTorrentUI = { Modal, Dropdown, Tooltip, activateTab };
  // Compatibility surface for the existing application modules. This is not
  // Bootstrap JS; it only mirrors the small API subset the app currently uses.
  window.bootstrap = { Modal, Dropdown, Tooltip };
})();
