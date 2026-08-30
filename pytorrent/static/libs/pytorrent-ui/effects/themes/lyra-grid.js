// Note: Registers the visual effect profile dedicated to the lyra-grid native PyTorrent theme.
window.pyTorrentThemeEffects?.register("lyra-grid", {
  ambient: "grid",
  progress: "scan",
  surface: "grid",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "grid",
    cursor: "ring",
    panels: "grid",
    logo: "shimmer",
    modal: "scan",
    rows: "grid",
    frame: "blueprint",
    strength: 0.88,
    trailRate: 56,
  },
  speed: 0.82,
  intensity: 0.5,
  density: 1.0,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
