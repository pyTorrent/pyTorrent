// Note: Registers the visual effect profile dedicated to the marshmallow-cloud native PyTorrent theme.
window.pyTorrentThemeEffects?.register("marshmallow-cloud", {
  ambient: "clouds",
  progress: "sheen",
  surface: "soft",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "fog",
    cursor: "bubble",
    panels: "soft",
    logo: "float",
    modal: "float",
    rows: "soft",
    frame: "prism",
    strength: 0.86,
    trailRate: 58,
  },
  speed: 0.54,
  intensity: 0.38,
  density: 0.86,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
