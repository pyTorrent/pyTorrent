// Note: Registers the visual effect profile dedicated to the railway native PyTorrent theme.
window.pyTorrentThemeEffects?.register("railway", {
  ambient: "grid",
  progress: "sweep",
  surface: "edge",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "grid",
    cursor: "comet",
    panels: "edge",
    logo: "shimmer",
    modal: "scan",
    rows: "grid",
    frame: "neon",
    strength: 0.90,
    trailRate: 58,
  },
  speed: 0.88,
  intensity: 0.46,
  density: 0.85,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
