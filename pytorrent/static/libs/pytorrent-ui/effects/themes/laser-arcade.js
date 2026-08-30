// Note: Registers the visual effect profile dedicated to the laser-arcade native PyTorrent theme.
window.pyTorrentThemeEffects?.register("laser-arcade", {
  ambient: "rays",
  progress: "laser",
  surface: "neon",
  controls: "pulse",
  burst: "spark",
  immersive: {
    scene: "laser",
    cursor: "spark",
    panels: "neon",
    logo: "glitch",
    modal: "warp",
    rows: "laser",
    frame: "neon",
    strength: 1.28,
    trailRate: 28,
  },
  speed: 1.35,
  intensity: 0.88,
  density: 1.15,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
