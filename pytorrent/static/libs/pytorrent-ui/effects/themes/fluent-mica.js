// Note: Registers the visual effect profile dedicated to the fluent-mica native PyTorrent theme.
window.pyTorrentThemeEffects?.register("fluent-mica", {
  ambient: "aurora",
  progress: "sheen",
  surface: "mica",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "mesh",
    cursor: "ring",
    panels: "mica",
    logo: "shimmer",
    modal: "hologram",
    rows: "soft",
    frame: "glass",
    strength: 0.78,
    trailRate: 64,
  },
  speed: 0.66,
  intensity: 0.38,
  density: 0.7,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
