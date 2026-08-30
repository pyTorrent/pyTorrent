// Note: Registers the visual effect profile dedicated to the prism-punch native PyTorrent theme.
window.pyTorrentThemeEffects?.register("prism-punch", {
  ambient: "rays",
  progress: "prism",
  surface: "neon",
  controls: "bounce",
  burst: "spark",
  immersive: {
    scene: "prism",
    cursor: "prism",
    panels: "hologram",
    logo: "rgb",
    modal: "warp",
    rows: "prism",
    frame: "rgb",
    strength: 1.34,
    trailRate: 26,
  },
  speed: 1.24,
  intensity: 0.9,
  density: 1.24,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
