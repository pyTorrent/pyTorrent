// Note: Registers the visual effect profile dedicated to the retro-diner native PyTorrent theme.
window.pyTorrentThemeEffects?.register("retro-diner", {
  ambient: "confetti",
  progress: "candy",
  surface: "chrome",
  controls: "bounce",
  burst: "star",
  immersive: {
    scene: "marquee",
    cursor: "star",
    panels: "chrome",
    logo: "chrome",
    modal: "pop",
    rows: "chrome",
    frame: "neon",
    strength: 1.06,
    trailRate: 40,
  },
  speed: 0.94,
  intensity: 0.62,
  density: 0.96,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
