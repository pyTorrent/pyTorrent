// Note: Registers the visual effect profile dedicated to the ice-pop native PyTorrent theme.
window.pyTorrentThemeEffects?.register("ice-pop", {
  ambient: "snow",
  progress: "sparkle",
  surface: "frost",
  controls: "float",
  burst: "star",
  immersive: {
    scene: "frost",
    cursor: "snow",
    panels: "frost",
    logo: "sparkle",
    modal: "frost",
    rows: "frost",
    frame: "frost",
    strength: 1.00,
    trailRate: 42,
  },
  speed: 0.82,
  intensity: 0.58,
  density: 1.0,
  colors: ["var(--pt-info)", "var(--pt-primary)", "var(--pt-text)", "var(--pt-success)"],
});
