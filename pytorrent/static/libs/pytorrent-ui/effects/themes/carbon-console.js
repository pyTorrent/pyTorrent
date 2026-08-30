// Note: Registers the visual effect profile dedicated to the carbon-console native PyTorrent theme.
window.pyTorrentThemeEffects?.register("carbon-console", {
  ambient: "scanlines",
  progress: "scan",
  surface: "terminal",
  controls: "terminal",
  burst: "pixel",
  immersive: {
    scene: "scan",
    cursor: "pixel",
    panels: "terminal",
    logo: "terminal",
    modal: "scan",
    rows: "terminal",
    frame: "chrome",
    strength: 0.98,
    trailRate: 46,
  },
  speed: 0.92,
  intensity: 0.48,
  density: 0.9,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
