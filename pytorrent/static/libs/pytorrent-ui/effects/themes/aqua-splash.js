// Note: Registers the visual effect profile dedicated to the aqua-splash native PyTorrent theme.
window.pyTorrentThemeEffects?.register("aqua-splash", {
  ambient: "waves",
  progress: "liquid",
  surface: "glass",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "caustics",
    cursor: "bubble",
    panels: "glass",
    logo: "wave",
    modal: "liquid",
    rows: "wave",
    frame: "aqua",
    strength: 1.02,
    trailRate: 46,
  },
  speed: 0.84,
  intensity: 0.66,
  density: 1.1,
  colors: ["var(--pt-info)", "var(--pt-primary)", "var(--pt-success)", "var(--pt-info)"],
});
