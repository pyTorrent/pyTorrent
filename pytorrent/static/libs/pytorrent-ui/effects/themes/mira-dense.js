// Note: Registers the visual effect profile dedicated to the mira-dense native PyTorrent theme.
window.pyTorrentThemeEffects?.register("mira-dense", {
  ambient: "scanlines",
  progress: "scan",
  surface: "edge",
  controls: "press",
  burst: "pixel",
  immersive: {
    scene: "scan",
    cursor: "pixel",
    panels: "edge",
    logo: "glitch",
    modal: "scan",
    rows: "scan",
    frame: "chrome",
    strength: 0.94,
    trailRate: 44,
  },
  speed: 1.2,
  intensity: 0.42,
  density: 1.2,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
