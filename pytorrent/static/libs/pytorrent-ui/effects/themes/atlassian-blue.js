// Note: Registers the visual effect profile dedicated to the atlassian-blue native PyTorrent theme.
window.pyTorrentThemeEffects?.register("atlassian-blue", {
  ambient: "waves",
  progress: "sweep",
  surface: "crisp",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "caustics",
    cursor: "comet",
    panels: "edge",
    logo: "wave",
    modal: "soft",
    rows: "wave",
    frame: "aqua",
    strength: 0.72,
    trailRate: 68,
  },
  speed: 0.72,
  intensity: 0.42,
  density: 0.72,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-primary-strong)"],
});
