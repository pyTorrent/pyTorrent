// Note: Registers the visual effect profile dedicated to the blueprint-lab native PyTorrent theme.
window.pyTorrentThemeEffects?.register("blueprint-lab", {
  ambient: "grid",
  progress: "scan",
  surface: "blueprint",
  controls: "press",
  burst: "ring",
  immersive: {
    scene: "blueprint",
    cursor: "ring",
    panels: "blueprint",
    logo: "shimmer",
    modal: "scan",
    rows: "grid",
    frame: "blueprint",
    strength: 0.94,
    trailRate: 50,
  },
  speed: 0.78,
  intensity: 0.54,
  density: 0.95,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-primary-strong)", "var(--pt-success)"],
});
