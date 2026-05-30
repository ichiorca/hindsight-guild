// Mermaid renderer service: POST /render { mermaid, look, theme, bg } -> PNG.
//
// Renders a Mermaid diagram via mermaid-cli (mmdc, which drives headless
// chromium). `look` selects the aesthetic:
//   - "handDrawn" -> excalidraw-style hand-drawn diagram
//   - "classic"   -> clean, flat infographic (the default mermaid look)
// Used by the ImageBrief agent for content-channel infographics because, unlike
// an image model, this produces LEGIBLE text labels.
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import express from "express";

const app = express();
app.use(express.json({ limit: "256kb" }));

app.get("/health", (_req, res) => res.status(200).send("ok"));

app.post("/render", async (req, res) => {
  const {
    mermaid,
    look = "handDrawn",
    theme = "default",
    bg = "white",
    scale = 2,
    width = 1400,
  } = req.body || {};
  if (!mermaid || typeof mermaid !== "string") {
    return res.status(400).json({ error: "body.mermaid (string) is required" });
  }
  const safeLook = look === "handDrawn" ? "handDrawn" : "classic";
  let dir;
  try {
    dir = await mkdtemp(join(tmpdir(), "mmd-"));
    const inp = join(dir, "in.mmd");
    const out = join(dir, "out.png");
    const cfg = join(dir, "config.json");
    // mmdc --configFile IS the mermaid config; `look` (mermaid v11) selects
    // the handDrawn/classic renderer.
    await writeFile(cfg, JSON.stringify({ look: safeLook, theme }));
    await writeFile(inp, mermaid);
    await new Promise((resolve, reject) => {
      execFile(
        "/srv/node_modules/.bin/mmdc",
        ["-i", inp, "-o", out, "-c", cfg, "-b", String(bg),
         "-s", String(scale), "-w", String(width),
         // chromium can't use its sandbox in a Cloud Run container.
         "-p", process.env.PUPPETEER_CONFIG || "/srv/puppeteer-config.json"],
        { timeout: 45_000 },
        (err, _stdout, stderr) => (err ? reject(new Error(stderr || String(err))) : resolve()),
      );
    });
    const png = await readFile(out);
    res.setHeader("Content-Type", "image/png");
    return res.status(200).send(png);
  } catch (e) {
    console.error("render failed:", String(e).slice(0, 600));
    return res.status(500).json({ error: String(e).slice(0, 600) });
  } finally {
    if (dir) rm(dir, { recursive: true, force: true }).catch(() => {});
  }
});

const port = process.env.PORT || 8080;
app.listen(port, () => console.log(`mermaid-renderer listening on ${port}`));
