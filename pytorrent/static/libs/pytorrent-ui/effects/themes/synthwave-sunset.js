// Note: Registers the visual effect profile dedicated to the synthwave-sunset native PyTorrent theme.
window.pyTorrentThemeEffects?.register("synthwave-sunset", {
  ambient: "grid",
  progress: "prism",
  surface: "neon",
  controls: "float",
  burst: "spark",
  immersive: {
    scene: "horizon",
    cursor: "prism",
    panels: "neon",
    logo: "neon",
    modal: "warp",
    rows: "prism",
    frame: "neon",
    strength: 1.24,
    trailRate: 30,
  },
  speed: 1.04,
  intensity: 0.78,
  density: 1.08,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
