// Note: Registers the visual effect profile dedicated to the mantine-indigo native PyTorrent theme.
window.pyTorrentThemeEffects?.register("mantine-indigo", {
  ambient: "aurora",
  progress: "pulse",
  surface: "glow",
  controls: "float",
  burst: "ring",
  immersive: {
    scene: "mesh",
    cursor: "ring",
    panels: "glow",
    logo: "pulse",
    modal: "float",
    rows: "glow",
    frame: "neon",
    strength: 0.86,
    trailRate: 56,
  },
  speed: 0.78,
  intensity: 0.5,
  density: 0.82,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
