// Note: Registers the visual effect profile dedicated to the maia-rounded native PyTorrent theme.
window.pyTorrentThemeEffects?.register("maia-rounded", {
  ambient: "bubbles",
  progress: "liquid",
  surface: "soft",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "bubbles",
    cursor: "bubble",
    panels: "soft",
    logo: "float",
    modal: "liquid",
    rows: "bubble",
    frame: "aqua",
    strength: 0.86,
    trailRate: 54,
  },
  speed: 0.7,
  intensity: 0.46,
  density: 1.0,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
