#!/usr/bin/env node
// Render the dashboard frame by frame into an mp4.
//
//   node video/capture.cjs --data /workspace/money-radar/data/out/data.json --out radar.mp4
//   options: --fps 60  --scale 1 (2 = supersample, then downscale)  --from 0 --to 24
//
// Needs Node 18+, ffmpeg, and Playwright with Chromium:
//   npm install && npx playwright install chromium
// render(t) is a pure function of time, so every frame is exact no matter
// how slow the machine is.
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const { chromium } = require("playwright");

const args = Object.fromEntries(process.argv.slice(2).reduce((a, x, i, arr) =>
  x.startsWith("--") ? a.concat([[x.slice(2), arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : true]]) : a, []));
const dataPath = args.data || path.join(process.env.RADAR_HOME || "/workspace/money-radar/data", "out/data.json");
const out = args.out || "radar.mp4";
const fps = +(args.fps || 60), scale = +(args.scale || 1);

(async () => {
  const data = fs.readFileSync(dataPath, "utf8");
  const tpl = fs.readFileSync(path.join(__dirname, "../radar/web/dashboard.html"), "utf8");
  const html = tpl.replace("/*__RADAR_DATA__*/null", data.replace(/<\//g, "<\\/"));
  const tmp = path.join(os.tmpdir(), `radar-capture-${process.pid}.html`);
  fs.writeFileSync(tmp, html);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1 });
  page.on("pageerror", e => { console.error("page error:", e.message); process.exitCode = 1; });
  await page.goto(`file://${tmp}?capture=1&scale=${scale}`);
  await page.evaluate(() => document.fonts.ready);
  const duration = await page.evaluate(() => window.DURATION);
  const from = +(args.from || 0), to = +(args.to || duration);
  const frames = Math.round((to - from) * fps);

  const vf = scale !== 1 ? ["-vf", "scale=1080:1350:flags=lanczos"] : [];
  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps), "-i", "-",
    ...vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
    { stdio: ["pipe", "inherit", "inherit"] });
  const canvas = page.locator("canvas");
  const t0 = Date.now();
  for (let i = 0; i < frames; i++) {
    await page.evaluate(t => window.render(t), from + i / fps);
    const buf = await canvas.screenshot({ type: "png" });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once("drain", r));
    if (i % fps === 0) process.stderr.write(`\r  frame ${i}/${frames}`);
  }
  ff.stdin.end();
  await new Promise(r => ff.on("close", r));
  await browser.close();
  fs.unlinkSync(tmp);
  console.error(`\r  ${frames} frames in ${((Date.now() - t0) / 1000).toFixed(0)}s → ${out}`);
})();
