// Note: Registers the visual effect profile dedicated to the sunset-grid native PyTorrent theme.
window.pyTorrentThemeEffects?.register("sunset-grid", {
  ambient: "grid",
  progress: "candy",
  surface: "glow",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "horizon",
    cursor: "comet",
    panels: "glow",
    logo: "neon",
    modal: "warp",
    rows: "prism",
    frame: "neon",
    strength: 1.02,
    trailRate: 46,
  },
  speed: 0.82,
  intensity: 0.58,
  density: 0.92,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
