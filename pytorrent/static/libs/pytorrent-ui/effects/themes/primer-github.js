// Note: Registers the visual effect profile dedicated to the primer-github native PyTorrent theme.
window.pyTorrentThemeEffects?.register("primer-github", {
  ambient: "grid",
  progress: "scan",
  surface: "crisp",
  controls: "press",
  burst: "ring",
  immersive: {
    scene: "grid",
    cursor: "ring",
    panels: "edge",
    logo: "shimmer",
    modal: "soft",
    rows: "grid",
    frame: "soft",
    strength: 0.62,
    trailRate: 82,
  },
  speed: 0.74,
  intensity: 0.3,
  density: 0.62,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
