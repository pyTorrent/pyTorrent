// Note: Registers the visual effect profile dedicated to the radix-mint native PyTorrent theme.
window.pyTorrentThemeEffects?.register("radix-mint", {
  ambient: "bubbles",
  progress: "sweep",
  surface: "soft",
  controls: "lift",
  burst: "ring",
  immersive: {
    scene: "bubbles",
    cursor: "ring",
    panels: "soft",
    logo: "shimmer",
    modal: "float",
    rows: "bubble",
    frame: "aqua",
    strength: 0.80,
    trailRate: 62,
  },
  speed: 0.7,
  intensity: 0.42,
  density: 0.8,
  colors: ["var(--pt-success)", "var(--pt-info)", "var(--pt-primary)", "var(--pt-success)"],
});
