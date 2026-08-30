// Note: Registers the visual effect profile dedicated to the bubblegum-blast native PyTorrent theme.
window.pyTorrentThemeEffects?.register("bubblegum-blast", {
  ambient: "bubbles",
  progress: "candy",
  surface: "sparkle",
  controls: "bounce",
  burst: "bubble",
  immersive: {
    scene: "bubbles",
    cursor: "bubble",
    panels: "sparkle",
    logo: "sparkle",
    modal: "pop",
    rows: "candy",
    frame: "prism",
    strength: 1.12,
    trailRate: 36,
  },
  speed: 1.02,
  intensity: 0.72,
  density: 1.25,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
