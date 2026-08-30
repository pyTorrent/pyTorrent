// Note: Registers the visual effect profile dedicated to the chakra-sage native PyTorrent theme.
window.pyTorrentThemeEffects?.register("chakra-sage", {
  ambient: "aurora",
  progress: "pulse",
  surface: "soft",
  controls: "float",
  burst: "bubble",
  immersive: {
    scene: "mesh",
    cursor: "bubble",
    panels: "soft",
    logo: "float",
    modal: "float",
    rows: "wave",
    frame: "soft",
    strength: 0.74,
    trailRate: 68,
  },
  speed: 0.66,
  intensity: 0.4,
  density: 0.75,
  colors: ["var(--pt-success)", "var(--pt-primary)", "var(--pt-info)", "var(--pt-success)"],
});
