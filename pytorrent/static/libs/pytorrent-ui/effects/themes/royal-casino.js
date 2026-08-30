// Note: Registers the visual effect profile dedicated to the royal-casino native PyTorrent theme.
window.pyTorrentThemeEffects?.register("royal-casino", {
  ambient: "casino",
  progress: "shimmer",
  surface: "gold",
  controls: "pulse",
  burst: "star",
  immersive: {
    scene: "marquee",
    cursor: "star",
    panels: "gold",
    logo: "gold",
    modal: "casino",
    rows: "gold",
    frame: "gold",
    strength: 1.22,
    trailRate: 30,
  },
  speed: 0.88,
  intensity: 0.78,
  density: 1.02,
  colors: ["var(--pt-warning)", "var(--pt-primary)", "var(--pt-success)", "var(--pt-warning)"],
});
