// Note: Registers the visual effect profile dedicated to the openai native PyTorrent theme.
window.pyTorrentThemeEffects?.register("openai", {
  ambient: "aurora",
  progress: "sheen",
  surface: "soft",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "mesh",
    cursor: "ring",
    panels: "soft",
    logo: "pulse",
    modal: "float",
    rows: "soft",
    frame: "soft",
    strength: 0.70,
    trailRate: 72,
  },
  speed: 0.7,
  intensity: 0.34,
  density: 0.65,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
