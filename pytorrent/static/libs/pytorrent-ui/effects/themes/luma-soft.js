// Note: Registers the visual effect profile dedicated to the luma-soft native PyTorrent theme.
window.pyTorrentThemeEffects?.register("luma-soft", {
  ambient: "aurora",
  progress: "sweep",
  surface: "soft",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "mesh",
    cursor: "bubble",
    panels: "soft",
    logo: "float",
    modal: "soft",
    rows: "wave",
    frame: "soft",
    strength: 0.72,
    trailRate: 72,
  },
  speed: 0.64,
  intensity: 0.34,
  density: 0.72,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
