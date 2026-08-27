(() => {
  'use strict';

  if (window.pyTorrentInitialLoaderProgress) return;

  const steps = Object.freeze({
    bootstrap: {percent: 4, label: 'Preparing interface'},
    interface: {percent: 24, label: 'Starting application'},
    connecting: {percent: 32, label: 'Connecting', autoMax: 40, autoDurationMs: 4500},
    connected: {percent: 42, label: 'Connection ready', autoMax: 48, autoDurationMs: 3500},
    profile: {percent: 50, label: 'Checking profile', autoMax: 54, autoDurationMs: 2500},
    snapshot_request: {percent: 56, label: 'Requesting torrent data', autoMax: 66, autoDurationMs: 4500},
    waiting_rtorrent: {percent: 68, label: 'Waiting for rTorrent', autoMax: 86, autoDurationMs: 9000},
    snapshot_received: {percent: 88, label: 'Snapshot received'},
    rendering: {percent: 94, label: 'Preparing torrent view', autoMax: 98, autoDurationMs: 1400},
    ready: {percent: 100, label: 'Ready'},
    profile_switch: {percent: 18, label: 'Switching profile', autoMax: 86, autoDurationMs: 10000},
  });

  let currentPercent = 0;
  let currentStep = '';
  let currentLabel = '';
  let autoAdvanceTimer = null;

  function elements() {
    return {
      root: document.getElementById('initialLoaderProgress'),
      track: document.getElementById('initialLoaderProgressTrack'),
      bar: document.getElementById('initialLoaderProgressBar'),
      label: document.getElementById('initialLoaderProgressLabel'),
      value: document.getElementById('initialLoaderProgressValue'),
    };
  }

  function render(stepName, percent, label) {
    const ui = elements();
    if (!ui.root || !ui.track || !ui.bar) return;
    const safePercent = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
    ui.root.dataset.step = stepName || '';
    ui.track.setAttribute('aria-valuenow', String(safePercent));
    ui.track.setAttribute('aria-valuetext', `${label || 'Loading'} ${safePercent}%`);
    ui.bar.style.width = `${safePercent}%`;
    if (ui.label) ui.label.textContent = label || 'Loading';
    if (ui.value) ui.value.textContent = `${safePercent}%`;
  }

  function stopAutoAdvance() {
    if (!autoAdvanceTimer) return;
    clearInterval(autoAdvanceTimer);
    autoAdvanceTimer = null;
  }

  function startAutoAdvance(stepName, step) {
    stopAutoAdvance();
    const maxPercent = Number(step?.autoMax || 0);
    const durationMs = Math.max(250, Number(step?.autoDurationMs || 0));
    if (!maxPercent || maxPercent <= currentPercent || stepName === 'ready') return;
    const startPercent = currentPercent;
    const startedAt = performance.now();
    autoAdvanceTimer = setInterval(() => {
      if (currentStep !== stepName) {
        stopAutoAdvance();
        return;
      }
      const elapsed = Math.max(0, performance.now() - startedAt);
      const eased = 1 - Math.exp(-elapsed / durationMs);
      const nextPercent = Math.min(maxPercent, startPercent + (maxPercent - startPercent) * eased);
      if (Math.round(nextPercent) === Math.round(currentPercent)) return;
      currentPercent = nextPercent;
      render(currentStep, currentPercent, currentLabel);
    }, 250);
  }

  function setStep(stepName, options = {}) {
    const step = steps[stepName];
    if (!step) return currentPercent;
    const reset = options.reset === true;
    if (reset) currentPercent = 0;
    // Note: Explicit stage percentages remain authoritative, while long network stages advance gradually without ever reaching the next real milestone.
    const requestedPercent = Number.isFinite(Number(options.percent)) ? Number(options.percent) : step.percent;
    currentPercent = Math.max(0, Math.min(100, requestedPercent));
    currentStep = stepName;
    currentLabel = options.label || step.label;
    render(currentStep, currentPercent, currentLabel);
    if (options.auto === false) stopAutoAdvance();
    else startAutoAdvance(currentStep, step);
    return currentPercent;
  }

  function reset(stepName = 'bootstrap') {
    stopAutoAdvance();
    currentPercent = 0;
    currentStep = '';
    currentLabel = '';
    return setStep(stepName, {reset: true});
  }

  function transitionDurationMs(element) {
    if (!element || typeof window.getComputedStyle !== 'function') return 0;
    const style = window.getComputedStyle(element);
    const parseTime = (value) => String(value || '').split(',').reduce((max, raw) => {
      const item = raw.trim();
      const amount = Number.parseFloat(item) || 0;
      const milliseconds = item.endsWith('ms') ? amount : amount * 1000;
      return Math.max(max, milliseconds);
    }, 0);
    return parseTime(style.transitionDuration) + parseTime(style.transitionDelay);
  }

  function waitForReadyPaint() {
    const bar = elements().bar;
    const durationMs = transitionDurationMs(bar);
    return new Promise((resolve) => {
      let done = false;
      let timeoutId = null;
      const finish = () => {
        if (done) return;
        done = true;
        if (timeoutId) clearTimeout(timeoutId);
        bar?.removeEventListener?.('transitionend', finish);
        resolve(currentStep === 'ready');
      };
      bar?.addEventListener?.('transitionend', finish, {once: true});
      // Note: The timeout fallback also covers background tabs where requestAnimationFrame may be heavily throttled.
      timeoutId = setTimeout(finish, Math.max(80, durationMs + 80));
      if (typeof requestAnimationFrame !== 'function') return;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (durationMs <= 0) finish();
      }));
    });
  }

  function complete() {
    // Note: Keep 100% visible through its final CSS transition and at least one paint before the full-screen loader is allowed to close.
    stopAutoAdvance();
    setStep('ready', {auto: false});
    return waitForReadyPaint();
  }

  // Note: Loader progress is framework-agnostic; Bootstrap and PyTorrent runtimes only consume this small public API.
  window.pyTorrentInitialLoaderProgress = {setStep, reset, complete};
  reset('bootstrap');
})();
