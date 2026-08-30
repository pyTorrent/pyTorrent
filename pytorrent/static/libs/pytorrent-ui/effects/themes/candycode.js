// Note: Registers the visual effect profile dedicated to the candycode native PyTorrent theme.
window.pyTorrentThemeEffects?.register("candycode", {
  ambient: "confetti",
  progress: "candy",
  surface: "sparkle",
  controls: "bounce",
  burst: "star",
  immersive: {
    scene: "confetti",
    cursor: "star",
    panels: "sparkle",
    logo: "sparkle",
    modal: "pop",
    rows: "candy",
    frame: "prism",
    strength: 1.08,
    trailRate: 38,
  },
  speed: 1.08,
  intensity: 0.72,
  density: 1.15,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
