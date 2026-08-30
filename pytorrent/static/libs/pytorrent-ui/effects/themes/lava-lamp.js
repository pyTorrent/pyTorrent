// Note: Registers the visual effect profile dedicated to the lava-lamp native PyTorrent theme.
window.pyTorrentThemeEffects?.register("lava-lamp", {
  ambient: "blobs",
  progress: "liquid",
  surface: "glow",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "blobs",
    cursor: "bubble",
    panels: "liquid",
    logo: "pulse",
    modal: "liquid",
    rows: "wave",
    frame: "neon",
    strength: 1.08,
    trailRate: 44,
  },
  speed: 0.56,
  intensity: 0.7,
  density: 1.0,
  colors: ["var(--pt-danger)", "var(--pt-warning)", "var(--pt-primary)", "var(--pt-danger)"],
});
