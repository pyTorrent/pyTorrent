// Note: Registers the visual effect profile dedicated to the space-disco native PyTorrent theme.
window.pyTorrentThemeEffects?.register("space-disco", {
  ambient: "stars",
  progress: "disco",
  surface: "neon",
  controls: "pulse",
  burst: "star",
  immersive: {
    scene: "stars",
    cursor: "star",
    panels: "neon",
    logo: "sparkle",
    modal: "warp",
    rows: "prism",
    frame: "rgb",
    strength: 1.26,
    trailRate: 28,
  },
  speed: 1.16,
  intensity: 0.86,
  density: 1.25,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
