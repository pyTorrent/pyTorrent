// Note: Registers the visual effect profile dedicated to the comic-burst native PyTorrent theme.
window.pyTorrentThemeEffects?.register("comic-burst", {
  ambient: "rays",
  progress: "pulse",
  surface: "comic",
  controls: "bounce",
  burst: "star",
  immersive: {
    scene: "comic",
    cursor: "star",
    panels: "comic",
    logo: "brutal",
    modal: "comic",
    rows: "brutal",
    frame: "brutal",
    strength: 1.10,
    trailRate: 36,
  },
  speed: 1.12,
  intensity: 0.74,
  density: 1.1,
  colors: ["var(--pt-primary)", "var(--pt-info)", "var(--pt-success)", "var(--pt-danger)"],
});
