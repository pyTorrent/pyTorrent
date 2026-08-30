// Note: Registers the visual effect profile dedicated to the material-ocean native PyTorrent theme.
window.pyTorrentThemeEffects?.register("material-ocean", {
  ambient: "waves",
  progress: "liquid",
  surface: "glow",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "caustics",
    cursor: "bubble",
    panels: "liquid",
    logo: "wave",
    modal: "liquid",
    rows: "wave",
    frame: "aqua",
    strength: 0.92,
    trailRate: 54,
  },
  speed: 0.72,
  intensity: 0.52,
  density: 0.92,
  colors: ["var(--pt-info)", "var(--pt-primary)", "var(--pt-success)", "var(--pt-info)"],
});
