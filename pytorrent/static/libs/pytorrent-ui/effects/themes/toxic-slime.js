// Note: Registers the visual effect profile dedicated to the toxic-slime native PyTorrent theme.
window.pyTorrentThemeEffects?.register("toxic-slime", {
  ambient: "blobs",
  progress: "liquid",
  surface: "slime",
  controls: "wobble",
  burst: "bubble",
  immersive: {
    scene: "slime",
    cursor: "slime",
    panels: "slime",
    logo: "pulse",
    modal: "liquid",
    rows: "slime",
    frame: "neon",
    strength: 1.16,
    trailRate: 34,
  },
  speed: 0.9,
  intensity: 0.68,
  density: 1.15,
  colors: ["var(--pt-success)", "var(--pt-info)", "var(--pt-warning)", "var(--pt-success)"],
});
