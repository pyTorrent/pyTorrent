// Note: Registers the visual effect profile dedicated to the base-ui native PyTorrent theme.
window.pyTorrentThemeEffects?.register("base-ui", {
  ambient: "grid",
  progress: "sweep",
  surface: "crisp",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "grid",
    cursor: "ring",
    panels: "edge",
    logo: "shimmer",
    modal: "scan",
    rows: "grid",
    frame: "blueprint",
    strength: 0.64,
    trailRate: 80,
  },
  speed: 0.7,
  intensity: 0.3,
  density: 0.65,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
