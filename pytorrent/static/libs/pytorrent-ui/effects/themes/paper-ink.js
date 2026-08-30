// Note: Registers the visual effect profile dedicated to the paper-ink native PyTorrent theme.
window.pyTorrentThemeEffects?.register("paper-ink", {
  ambient: "paper",
  progress: "sheen",
  surface: "crisp",
  controls: "press",
  burst: "ink",
  immersive: {
    scene: "paper",
    cursor: "ink",
    panels: "paper",
    logo: "paper",
    modal: "paper",
    rows: "paper",
    frame: "paper",
    strength: 0.68,
    trailRate: 78,
  },
  speed: 0.58,
  intensity: 0.26,
  density: 0.55,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
