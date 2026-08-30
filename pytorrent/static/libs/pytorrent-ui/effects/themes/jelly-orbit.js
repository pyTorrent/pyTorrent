// Note: Registers the visual effect profile dedicated to the jelly-orbit native PyTorrent theme.
window.pyTorrentThemeEffects?.register("jelly-orbit", {
  ambient: "orbit",
  progress: "liquid",
  surface: "glow",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "orbit",
    cursor: "bubble",
    panels: "liquid",
    logo: "orbit",
    modal: "liquid",
    rows: "wave",
    frame: "prism",
    strength: 1.04,
    trailRate: 42,
  },
  speed: 0.78,
  intensity: 0.64,
  density: 1.0,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
