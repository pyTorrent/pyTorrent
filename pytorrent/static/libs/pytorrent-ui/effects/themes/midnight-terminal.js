// Note: Registers the visual effect profile dedicated to the midnight-terminal native PyTorrent theme.
window.pyTorrentThemeEffects?.register("midnight-terminal", {
  ambient: "matrix",
  progress: "scan",
  surface: "terminal",
  controls: "terminal",
  burst: "pixel",
  immersive: {
    scene: "matrix",
    cursor: "pixel",
    panels: "terminal",
    logo: "glitch",
    modal: "terminal",
    rows: "terminal",
    frame: "terminal",
    strength: 1.12,
    trailRate: 40,
  },
  speed: 1.12,
  intensity: 0.68,
  density: 1.1,
  colors: ["var(--pt-success)", "var(--pt-primary)", "var(--pt-info)", "var(--pt-success)"],
});
