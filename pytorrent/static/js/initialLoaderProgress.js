(() => {
  'use strict';

  if (window.pyTorrentInitialLoaderProgress) return;

  const steps = Object.freeze({
    bootstrap: {percent: 8, label: 'Preparing interface'},
    interface: {percent: 18, label: 'Starting application'},
    connecting: {percent: 32, label: 'Connecting'},
    connected: {percent: 45, label: 'Connection ready'},
    profile: {percent: 55, label: 'Checking profile'},
    snapshot_request: {percent: 65, label: 'Requesting torrent data'},
    waiting_rtorrent: {percent: 72, label: 'Waiting for rTorrent'},
    snapshot_received: {percent: 84, label: 'Snapshot received'},
    rendering: {percent: 94, label: 'Preparing torrent view'},
    ready: {percent: 100, label: 'Ready'},
    profile_switch: {percent: 20, label: 'Switching profile'},
  });

  let currentPercent = 0;
  let currentStep = '';

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

  function setStep(stepName, options = {}) {
    const step = steps[stepName];
    if (!step) return currentPercent;
    const reset = options.reset === true;
    if (reset) currentPercent = 0;
    // Note: Recovery and reconnect steps may move backward because the percentage represents the current real loading stage, not elapsed time.
    currentPercent = step.percent;
    currentStep = stepName;
    render(currentStep, currentPercent, options.label || step.label);
    return currentPercent;
  }

  function reset(stepName = 'bootstrap') {
    currentPercent = 0;
    currentStep = '';
    return setStep(stepName, {reset: true});
  }

  function complete() {
    return setStep('ready');
  }

  // Note: Loader progress is framework-agnostic; Bootstrap and PyTorrent runtimes only consume this small public API.
  window.pyTorrentInitialLoaderProgress = {setStep, reset, complete};
  reset('bootstrap');
})();
