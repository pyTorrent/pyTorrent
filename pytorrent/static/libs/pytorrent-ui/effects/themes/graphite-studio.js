// Note: Registers the visual effect profile dedicated to the graphite-studio native PyTorrent theme.
window.pyTorrentThemeEffects?.register("graphite-studio", {
  ambient: "scanlines",
  progress: "scan",
  surface: "edge",
  controls: "press",
  burst: "ring",
  immersive: {
    scene: "scan",
    cursor: "comet",
    panels: "edge",
    logo: "shimmer",
    modal: "scan",
    rows: "scan",
    frame: "chrome",
    strength: 0.82,
    trailRate: 62,
  },
  speed: 0.78,
  intensity: 0.38,
  density: 0.75,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
