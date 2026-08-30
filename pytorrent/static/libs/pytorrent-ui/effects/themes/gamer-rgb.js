// Note: Registers the visual effect profile dedicated to the gamer-rgb native PyTorrent theme.
window.pyTorrentThemeEffects?.register("gamer-rgb", {
  ambient: "rays",
  progress: "rgb",
  surface: "rgb",
  controls: "pulse",
  burst: "spark",
  immersive: {
    scene: "vortex",
    cursor: "prism",
    panels: "hologram",
    logo: "rgb",
    modal: "warp",
    rows: "laser",
    frame: "rgb",
    strength: 1.38,
    trailRate: 24,
  },
  speed: 1.46,
  intensity: 1.0,
  density: 1.35,
  colors: ["var(--pytorrent-theme-progress-start, var(--pt-info))", "var(--pytorrent-theme-progress-mid, var(--pt-primary))", "var(--pytorrent-theme-progress-end, var(--pt-danger))", "var(--pt-success)"],
});
