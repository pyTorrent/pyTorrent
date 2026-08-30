// Note: Registers the visual effect profile dedicated to the cyber-sakura native PyTorrent theme.
window.pyTorrentThemeEffects?.register("cyber-sakura", {
  ambient: "petals",
  progress: "candy",
  surface: "neon",
  controls: "float",
  burst: "star",
  immersive: {
    scene: "petals",
    cursor: "petal",
    panels: "neon",
    logo: "sparkle",
    modal: "warp",
    rows: "neon",
    frame: "prism",
    strength: 1.12,
    trailRate: 36,
  },
  speed: 0.92,
  intensity: 0.68,
  density: 1.05,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
