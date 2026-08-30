// Note: Registers the visual effect profile dedicated to the neobrutal-pop native PyTorrent theme.
window.pyTorrentThemeEffects?.register("neobrutal-pop", {
  ambient: "confetti",
  progress: "pixel",
  surface: "brutal",
  controls: "bounce",
  burst: "pixel",
  immersive: {
    scene: "confetti",
    cursor: "pixel",
    panels: "brutal",
    logo: "brutal",
    modal: "brutal",
    rows: "brutal",
    frame: "brutal",
    strength: 1.12,
    trailRate: 36,
  },
  speed: 1.16,
  intensity: 0.72,
  density: 1.2,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
