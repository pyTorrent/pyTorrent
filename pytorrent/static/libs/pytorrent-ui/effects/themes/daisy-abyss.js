// Note: Registers the visual effect profile dedicated to the daisy-abyss native PyTorrent theme.
window.pyTorrentThemeEffects?.register("daisy-abyss", {
  ambient: "stars",
  progress: "glow",
  surface: "neon",
  controls: "float",
  burst: "star",
  immersive: {
    scene: "stars",
    cursor: "star",
    panels: "neon",
    logo: "orbit",
    modal: "warp",
    rows: "neon",
    frame: "neon",
    strength: 1.00,
    trailRate: 44,
  },
  speed: 0.86,
  intensity: 0.66,
  density: 1.0,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
