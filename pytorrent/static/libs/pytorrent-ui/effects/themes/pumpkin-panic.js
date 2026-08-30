// Note: Registers the visual effect profile dedicated to the pumpkin-panic native PyTorrent theme.
window.pyTorrentThemeEffects?.register("pumpkin-panic", {
  ambient: "embers",
  progress: "pulse",
  surface: "glow",
  controls: "bounce",
  burst: "spark",
  immersive: {
    scene: "embers",
    cursor: "ember",
    panels: "glow",
    logo: "pulse",
    modal: "pop",
    rows: "glow",
    frame: "gold",
    strength: 1.14,
    trailRate: 34,
  },
  speed: 1.06,
  intensity: 0.72,
  density: 1.08,
  colors: ["var(--pt-warning)", "var(--pt-danger)", "var(--pt-primary)", "var(--pt-warning)"],
});
