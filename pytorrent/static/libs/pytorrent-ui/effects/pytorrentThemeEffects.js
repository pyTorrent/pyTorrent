(function () {
  'use strict';

  const registry = new Map();
  const loadPromises = new Map();
  const root = document.documentElement;
  const MANAGED_DATA_ATTRIBUTES = [
    'pytorrentEffectAmbient',
    'pytorrentEffectProgress',
    'pytorrentEffectSurface',
    'pytorrentEffectControls',
    'pytorrentEffectBurst',
    'pytorrentImmersiveScene',
    'pytorrentImmersiveCursor',
    'pytorrentImmersivePanels',
    'pytorrentImmersiveLogo',
    'pytorrentImmersiveModal',
    'pytorrentImmersiveRows',
    'pytorrentImmersiveFrame',
  ];
  const MANAGED_STYLE_PROPERTIES = [
    '--ptfx-color-1',
    '--ptfx-color-2',
    '--ptfx-color-3',
    '--ptfx-color-4',
    '--ptfx-duration',
    '--ptfx-duration-fast',
    '--ptfx-duration-slow',
    '--ptfx-intensity',
    '--ptfx-layer-opacity',
    '--ptfx-particle-size',
    '--ptfx-grid-size',
    '--ptfx-blur',
    '--ptfx-immersive-intensity',
    '--ptfx-trail-size',
    '--ptfx-pointer-x',
    '--ptfx-pointer-y',
  ];
  let activationToken = 0;
  let layer = null;
  let immersiveFrame = null;
  let pointerDownHandlerAttached = false;
  let pointerMoveHandlerAttached = false;
  let lastTrailAt = 0;
  let trailIntervalMs = 52;

  // Note: Only server-registered native theme effect URLs are considered supported, preventing arbitrary script paths from being loaded.
  function effectUrls() {
    const urls = window.PYTORRENT?.pytorrentThemeEffectUrls;
    return urls && typeof urls === 'object' ? urls : {};
  }

  // Note: Normalize the optional immersive section separately so every theme can declaratively tune its stronger second visual level.
  function normalizeImmersive(rawValue) {
    const raw = rawValue && typeof rawValue === 'object' ? rawValue : {};
    return {
      scene: String(raw.scene || 'mesh'),
      cursor: String(raw.cursor || 'ring'),
      panels: String(raw.panels || 'soft'),
      logo: String(raw.logo || 'pulse'),
      modal: String(raw.modal || 'soft'),
      rows: String(raw.rows || 'soft'),
      frame: String(raw.frame || 'soft'),
      strength: Math.max(0.45, Math.min(1.45, Number(raw.strength || 0.85))),
      trailRate: Math.max(24, Math.min(90, Number(raw.trailRate || 52))),
    };
  }

  // Note: Normalize effect profiles once so every theme script can stay declarative and the common runtime owns safe defaults.
  function normalizeProfile(profile) {
    const raw = profile && typeof profile === 'object' ? profile : {};
    const colors = Array.isArray(raw.colors) ? raw.colors.filter(Boolean).slice(0, 4) : [];
    while (colors.length < 4) {
      colors.push([
        'var(--pt-primary)',
        'var(--pt-info)',
        'var(--pt-success)',
        'var(--pt-danger)',
      ][colors.length]);
    }
    const speed = Math.max(0.55, Math.min(2.2, Number(raw.speed || 1)));
    const intensity = Math.max(0.2, Math.min(1.3, Number(raw.intensity || 0.65)));
    const density = Math.max(0.4, Math.min(1.6, Number(raw.density || 1)));
    return {
      ambient: String(raw.ambient || 'aurora'),
      progress: String(raw.progress || 'sweep'),
      surface: String(raw.surface || 'soft'),
      controls: String(raw.controls || 'lift'),
      burst: String(raw.burst || 'ring'),
      colors,
      speed,
      intensity,
      density,
      immersive: normalizeImmersive(raw.immersive),
    };
  }

  // Note: Theme files register data only; lifecycle, DOM mutations and cleanup remain centralized in this manager.
  function register(theme, profile) {
    const key = String(theme || '').trim();
    if (!key || !Object.prototype.hasOwnProperty.call(effectUrls(), key)) return false;
    registry.set(key, normalizeProfile(profile));
    return true;
  }

  // Note: Compatible Bootstrap skins deliberately have no entry in the native effect URL map and therefore never expose theme effects.
  function supports(theme) {
    const key = String(theme || '').trim();
    return !!key && Object.prototype.hasOwnProperty.call(effectUrls(), key);
  }

  // Note: The visual layer is created lazily only while effects are active, keeping the default/off path DOM-neutral.
  function ensureLayer() {
    if (layer?.isConnected) return layer;
    const shell = document.querySelector('.app-shell');
    if (!shell) return null;
    layer = document.createElement('div');
    layer.id = 'pytorrentThemeEffectsLayer';
    layer.className = 'pytorrent-theme-effects-layer';
    layer.setAttribute('aria-hidden', 'true');
    for (let index = 0; index < 14; index += 1) {
      const particle = document.createElement('span');
      particle.className = 'pytorrent-theme-effect-particle';
      particle.dataset.ptfxParticle = String(index + 1);
      layer.appendChild(particle);
    }
    shell.appendChild(layer);
    return layer;
  }

  // Note: The immersive frame is owned by the shared overlay and is created only for second-level effects.
  function ensureImmersiveFrame() {
    const activeLayer = ensureLayer();
    if (!activeLayer) return null;
    if (immersiveFrame?.isConnected) return immersiveFrame;
    immersiveFrame = document.createElement('span');
    immersiveFrame.className = 'pytorrent-theme-immersive-frame';
    activeLayer.appendChild(immersiveFrame);
    return immersiveFrame;
  }

  // Note: Per-theme tuning is expressed through temporary root variables so no inline styling leaks onto application components.
  function applyProfileVariables(profile) {
    const duration = 8 / profile.speed;
    root.style.setProperty('--ptfx-color-1', profile.colors[0]);
    root.style.setProperty('--ptfx-color-2', profile.colors[1]);
    root.style.setProperty('--ptfx-color-3', profile.colors[2]);
    root.style.setProperty('--ptfx-color-4', profile.colors[3]);
    root.style.setProperty('--ptfx-duration', `${duration.toFixed(2)}s`);
    root.style.setProperty('--ptfx-duration-fast', `${Math.max(1.4, duration * 0.42).toFixed(2)}s`);
    root.style.setProperty('--ptfx-duration-slow', `${Math.max(5, duration * 1.75).toFixed(2)}s`);
    root.style.setProperty('--ptfx-intensity', String(profile.intensity));
    root.style.setProperty('--ptfx-layer-opacity', String(Math.min(0.54, 0.13 + profile.intensity * 0.24)));
    root.style.setProperty('--ptfx-particle-size', `${Math.round(5 + profile.density * 5)}px`);
    root.style.setProperty('--ptfx-grid-size', `${Math.round(24 + (1.6 - profile.density) * 16)}px`);
    root.style.setProperty('--ptfx-blur', `${Math.round(18 + profile.intensity * 22)}px`);
    root.style.setProperty('--ptfx-immersive-intensity', String(profile.immersive.strength));
    root.style.setProperty('--ptfx-trail-size', `${Math.round(7 + profile.density * 5 + profile.immersive.strength * 2)}px`);
    root.style.setProperty('--ptfx-pointer-x', '50%');
    root.style.setProperty('--ptfx-pointer-y', '50%');
    trailIntervalMs = profile.immersive.trailRate;
  }

  // Note: Dynamic theme scripts are loaded once and cached; the backend-provided URL map remains the only source of executable paths.
  function loadTheme(theme) {
    const key = String(theme || '').trim();
    if (registry.has(key)) return Promise.resolve(registry.get(key));
    if (loadPromises.has(key)) return loadPromises.get(key);
    const url = effectUrls()[key];
    if (!url) return Promise.reject(new Error(`No visual effects are registered for theme ${key}`));
    const promise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.async = true;
      script.src = url;
      script.dataset.pytorrentThemeEffect = key;
      script.onload = () => {
        const profile = registry.get(key);
        if (profile) resolve(profile);
        else reject(new Error(`Theme effect module ${key} did not register a profile`));
      };
      script.onerror = () => reject(new Error(`Failed to load visual effects for theme ${key}`));
      document.head.appendChild(script);
    }).catch((error) => {
      loadPromises.delete(key);
      document.querySelector(`script[data-pytorrent-theme-effect="${key}"]`)?.remove();
      throw error;
    });
    loadPromises.set(key, promise);
    return promise;
  }

  // Note: Click bursts reuse the single overlay layer and self-remove, avoiding listeners or nodes attached to individual controls and torrent rows.
  function createBurst(event) {
    if (!layer?.isConnected || root.dataset.pytorrentMotion !== 'on') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const shell = layer.parentElement;
    if (!shell) return;
    const rect = shell.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) return;
    const burst = document.createElement('i');
    burst.className = 'pytorrent-theme-effect-burst';
    burst.style.left = `${event.clientX - rect.left}px`;
    burst.style.top = `${event.clientY - rect.top}px`;
    layer.appendChild(burst);
    window.setTimeout(() => burst.remove(), 1100);
    const bursts = layer.querySelectorAll('.pytorrent-theme-effect-burst');
    if (bursts.length > 18) bursts[0].remove();
  }

  // Note: Immersive pointer movement updates one shared spotlight and emits throttled, self-removing theme-specific trail particles only for fine pointers.
  function createTrail(event) {
    if (root.dataset.pytorrentImmersiveEffects !== 'on' || root.dataset.pytorrentMotion !== 'on') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    if (window.matchMedia?.('(pointer: fine)').matches === false) return;
    if (!layer?.isConnected) return;
    const shell = layer.parentElement;
    if (!shell) return;
    const rect = shell.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return;
    root.style.setProperty('--ptfx-pointer-x', `${x}px`);
    root.style.setProperty('--ptfx-pointer-y', `${y}px`);
    const now = performance.now();
    if (now - lastTrailAt < trailIntervalMs) return;
    lastTrailAt = now;
    const trail = document.createElement('i');
    trail.className = 'pytorrent-theme-effect-trail';
    trail.style.left = `${x}px`;
    trail.style.top = `${y}px`;
    layer.appendChild(trail);
    window.setTimeout(() => trail.remove(), 900);
    const trails = layer.querySelectorAll('.pytorrent-theme-effect-trail');
    if (trails.length > 28) trails[0].remove();
  }

  // Note: Base clicks always use one delegated listener, while the higher-frequency pointer-move listener exists only when immersive mode is active.
  function attachPointerEffects(immersiveEnabled) {
    if (!pointerDownHandlerAttached) {
      document.addEventListener('pointerdown', createBurst, { passive: true });
      pointerDownHandlerAttached = true;
    }
    if (immersiveEnabled && !pointerMoveHandlerAttached) {
      document.addEventListener('pointermove', createTrail, { passive: true });
      pointerMoveHandlerAttached = true;
    }
  }

  // Note: Listener cleanup mirrors initialization exactly so repeated theme switches never accumulate event handlers.
  function detachPointerEffects() {
    if (pointerDownHandlerAttached) document.removeEventListener('pointerdown', createBurst);
    if (pointerMoveHandlerAttached) document.removeEventListener('pointermove', createTrail);
    pointerDownHandlerAttached = false;
    pointerMoveHandlerAttached = false;
    lastTrailAt = 0;
  }

  // Note: Immersive data attributes are applied only after the base profile is active, keeping the second level strictly dependent on level one.
  function applyImmersiveProfile(profile, enabled) {
    root.dataset.pytorrentImmersiveEffects = enabled ? 'on' : 'off';
    if (!enabled) return;
    const immersive = profile.immersive;
    root.dataset.pytorrentImmersiveScene = immersive.scene;
    root.dataset.pytorrentImmersiveCursor = immersive.cursor;
    root.dataset.pytorrentImmersivePanels = immersive.panels;
    root.dataset.pytorrentImmersiveLogo = immersive.logo;
    root.dataset.pytorrentImmersiveModal = immersive.modal;
    root.dataset.pytorrentImmersiveRows = immersive.rows;
    root.dataset.pytorrentImmersiveFrame = immersive.frame;
    ensureImmersiveFrame();
  }

  // Note: Reset only state owned by visual effects so theme switches can cleanly replace a profile without touching application DOM or preferences.
  function resetVisualState(status) {
    root.dataset.pytorrentThemeEffects = status;
    root.dataset.pytorrentImmersiveEffects = 'off';
    MANAGED_DATA_ATTRIBUTES.forEach((name) => { delete root.dataset[name]; });
    MANAGED_STYLE_PROPERTIES.forEach((name) => root.style.removeProperty(name));
    detachPointerEffects();
    layer?.remove();
    layer = null;
    immersiveFrame = null;
  }

  // Note: Clear every DOM node, listener, data attribute and CSS variable owned by theme effects while cancelling pending activations.
  function clear() {
    activationToken += 1;
    resetVisualState('off');
  }

  // Note: Activate both visual levels atomically after lazy loading, while stale asynchronous loads are ignored after later switches.
  async function apply(options) {
    const requested = options && typeof options === 'object' ? options : {};
    const theme = String(requested.theme || root.dataset.pytorrentTheme || '').trim();
    const enabled = !!requested.enabled;
    const immersiveEnabled = enabled && !!requested.immersiveEnabled;
    const token = ++activationToken;
    if (!enabled || root.dataset.uiFramework !== 'pytorrent' || !supports(theme)) {
      clear();
      return false;
    }
    resetVisualState('loading');
    try {
      const profile = await loadTheme(theme);
      if (token !== activationToken) return false;
      if (root.dataset.uiFramework !== 'pytorrent' || root.dataset.pytorrentTheme !== theme) return false;
      root.dataset.pytorrentThemeEffects = 'on';
      root.dataset.pytorrentEffectAmbient = profile.ambient;
      root.dataset.pytorrentEffectProgress = profile.progress;
      root.dataset.pytorrentEffectSurface = profile.surface;
      root.dataset.pytorrentEffectControls = profile.controls;
      root.dataset.pytorrentEffectBurst = profile.burst;
      applyProfileVariables(profile);
      ensureLayer();
      applyImmersiveProfile(profile, immersiveEnabled);
      attachPointerEffects(immersiveEnabled);
      return true;
    } catch (error) {
      if (token === activationToken) clear();
      console.warn('[pyTorrent] Theme visual effects could not be activated.', error);
      return false;
    }
  }

  window.pyTorrentThemeEffects = Object.freeze({
    register,
    supports,
    apply,
    clear,
  });
})();
