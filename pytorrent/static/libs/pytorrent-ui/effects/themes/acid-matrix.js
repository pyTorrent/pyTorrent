// Note: Registers the visual effect profile dedicated to the acid-matrix native PyTorrent theme.
window.pyTorrentThemeEffects?.register("acid-matrix", {
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
    modal: "glitch",
    rows: "terminal",
    frame: "terminal",
    strength: 1.30,
    trailRate: 28,
  },
  speed: 1.32,
  intensity: 0.86,
  density: 1.25,
  colors: ["var(--pt-success)", "var(--pt-info)", "var(--pt-primary)", "var(--pt-success)"],
});
