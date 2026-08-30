// Note: Registers the visual effect profile dedicated to the daisy-caramel native PyTorrent theme.
window.pyTorrentThemeEffects?.register("daisy-caramel", {
  ambient: "bubbles",
  progress: "liquid",
  surface: "soft",
  controls: "bounce",
  burst: "bubble",
  immersive: {
    scene: "bubbles",
    cursor: "bubble",
    panels: "liquid",
    logo: "sparkle",
    modal: "liquid",
    rows: "bubble",
    frame: "gold",
    strength: 0.90,
    trailRate: 52,
  },
  speed: 0.76,
  intensity: 0.48,
  density: 1.0,
  colors: ["var(--pt-warning)", "var(--pt-primary)", "var(--pt-success)", "var(--pt-danger)"],
});
