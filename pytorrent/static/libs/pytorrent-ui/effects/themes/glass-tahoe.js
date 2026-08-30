// Note: Registers the visual effect profile dedicated to the glass-tahoe native PyTorrent theme.
window.pyTorrentThemeEffects?.register("glass-tahoe", {
  ambient: "waves",
  progress: "liquid",
  surface: "glass",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "caustics",
    cursor: "bubble",
    panels: "glass",
    logo: "shimmer",
    modal: "hologram",
    rows: "wave",
    frame: "glass",
    strength: 0.82,
    trailRate: 62,
  },
  speed: 0.62,
  intensity: 0.42,
  density: 0.78,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
