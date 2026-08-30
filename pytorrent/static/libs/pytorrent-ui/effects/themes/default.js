// Note: Registers the visual effect profile dedicated to the default native PyTorrent theme.
window.pyTorrentThemeEffects?.register("default", {
  ambient: "aurora",
  progress: "sweep",
  surface: "soft",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "mesh",
    cursor: "ring",
    panels: "soft",
    logo: "pulse",
    modal: "soft",
    rows: "soft",
    frame: "soft",
    strength: 0.78,
    trailRate: 66,
  },
  speed: 0.82,
  intensity: 0.42,
  density: 0.8,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-primary-strong)"],
});
