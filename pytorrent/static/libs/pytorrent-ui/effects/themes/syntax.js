// Note: Registers the visual effect profile dedicated to the syntax native PyTorrent theme.
window.pyTorrentThemeEffects?.register("syntax", {
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
    frame: "terminal",
    strength: 0.92,
    trailRate: 50,
  },
  speed: 0.94,
  intensity: 0.5,
  density: 0.9,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
