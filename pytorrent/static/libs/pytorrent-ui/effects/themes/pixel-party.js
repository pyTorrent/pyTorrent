// Note: Registers the visual effect profile dedicated to the pixel-party native PyTorrent theme.
window.pyTorrentThemeEffects?.register("pixel-party", {
  ambient: "pixel",
  progress: "pixel",
  surface: "pixel",
  controls: "bounce",
  burst: "pixel",
  immersive: {
    scene: "pixel",
    cursor: "pixel",
    panels: "pixel",
    logo: "pixel",
    modal: "pixel",
    rows: "pixel",
    frame: "pixel",
    strength: 1.18,
    trailRate: 32,
  },
  speed: 1.28,
  intensity: 0.74,
  density: 1.35,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
