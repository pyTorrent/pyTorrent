// Note: Registers the visual effect profile dedicated to the nova-compact native PyTorrent theme.
window.pyTorrentThemeEffects?.register("nova-compact", {
  ambient: "stars",
  progress: "pulse",
  surface: "edge",
  controls: "quick",
  burst: "star",
  immersive: {
    scene: "stars",
    cursor: "star",
    panels: "edge",
    logo: "orbit",
    modal: "warp",
    rows: "laser",
    frame: "neon",
    strength: 0.92,
    trailRate: 48,
  },
  speed: 1.18,
  intensity: 0.52,
  density: 0.9,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
