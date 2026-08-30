// Note: Registers the visual effect profile dedicated to the material-you native PyTorrent theme.
window.pyTorrentThemeEffects?.register("material-you", {
  ambient: "blobs",
  progress: "liquid",
  surface: "soft",
  controls: "ripple",
  burst: "bubble",
  immersive: {
    scene: "blobs",
    cursor: "bubble",
    panels: "liquid",
    logo: "pulse",
    modal: "liquid",
    rows: "wave",
    frame: "soft",
    strength: 0.88,
    trailRate: 56,
  },
  speed: 0.68,
  intensity: 0.48,
  density: 0.9,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
