// Note: Registers the visual effect profile dedicated to the ant-enterprise native PyTorrent theme.
window.pyTorrentThemeEffects?.register("ant-enterprise", {
  ambient: "grid",
  progress: "sweep",
  surface: "crisp",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "grid",
    cursor: "ring",
    panels: "edge",
    logo: "pulse",
    modal: "soft",
    rows: "grid",
    frame: "soft",
    strength: 0.66,
    trailRate: 76,
  },
  speed: 0.72,
  intensity: 0.3,
  density: 0.62,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
