/* Project JAL — sign-in background.
   A monsoon storm breaking up over the fields, drawn once in code (no image files) in the same
   navy-to-teal colours as the opening intro, so the intro hands over to this page without a colour
   jump. It's a still picture: it's only redrawn when the window size changes. */
(function () {
  "use strict";

  function rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function draw(cv) {
    var D = Math.min(2, window.devicePixelRatio || 1);
    var W = window.innerWidth, H = window.innerHeight;
    if (W < 2 || H < 2) return false; // window not laid out yet (e.g. a hidden tab); drawn on the next resize
    cv.width = Math.round(W * D); cv.height = Math.round(H * D);
    var x = cv.getContext("2d");
    x.setTransform(D, 0, 0, D, 0, 0);
    var r = rng(7), S = Math.max(W, H);

    function lin(x0, y0, x1, y1, stops) {
      var g = x.createLinearGradient(x0, y0, x1, y1);
      stops.forEach(function (s) { g.addColorStop(s[0], s[1]); });
      return g;
    }
    // Soft round puffs; many overlapping ones make a cloud bank.
    function puffs(n, box, rad, rgb, a, op) {
      x.save();
      if (op) x.globalCompositeOperation = op;
      for (var i = 0; i < n; i++) {
        var cx = box[0] + (box[2] - box[0]) * r();
        var cy = box[1] + (box[3] - box[1]) * Math.pow(r(), box[4] || 1);
        var rr = rad[0] + (rad[1] - rad[0]) * r();
        var g = x.createRadialGradient(cx, cy, 0, cx, cy, rr);
        g.addColorStop(0, "rgba(" + rgb + "," + a + ")");
        g.addColorStop(0.55, "rgba(" + rgb + "," + a * 0.55 + ")");
        g.addColorStop(1, "rgba(" + rgb + ",0)");
        x.fillStyle = g; x.beginPath(); x.arc(cx, cy, rr, 0, Math.PI * 2); x.fill();
      }
      x.restore();
    }
    function hill(base, amp, waves, fill) {
      x.beginPath(); x.moveTo(0, H);
      for (var i = 0; i <= W + 4; i += 4) {
        var y = base;
        waves.forEach(function (w) { y += amp * w[0] * Math.sin((i / W) * Math.PI * w[1] + w[2]); });
        x.lineTo(i, y);
      }
      x.lineTo(W, H); x.closePath(); x.fillStyle = fill; x.fill();
    }

    // Where the light breaks through: in the gap between the headline and the sign-in card on wide
    // screens, centred above the form on narrow ones.
    var cx = W > 860 ? W * 0.47 : W * 0.5, cy = H * (W > 860 ? 0.34 : 0.3);

    // sky
    x.fillStyle = lin(0, 0, 0, H, [[0, "#07172a"], [0.34, "#10304d"], [0.56, "#245a7d"], [0.69, "#7fb9cf"], [0.74, "#c6e3ea"]]);
    x.fillRect(0, 0, W, H);
    var core = x.createRadialGradient(cx, cy, 0, cx, cy, S * 0.34);
    core.addColorStop(0, "rgba(240, 249, 255, .95)"); core.addColorStop(0.25, "rgba(200, 232, 250, .45)"); core.addColorStop(1, "rgba(200, 232, 250, 0)");
    x.fillStyle = core; x.fillRect(0, 0, W, H);

    // cloud banks: dark body, silver edges near the opening, heavier cloud along the top
    var n = Math.round((W * H) / 5200);
    puffs(n, [-W * 0.05, -H * 0.1, W * 1.05, H * 0.5, 1.6], [S * 0.05, S * 0.12], "12, 28, 46", 0.44);
    puffs(Math.round(n * 0.35), [W * 0.22, H * 0.18, W * 0.88, H * 0.5], [S * 0.03, S * 0.075], "205, 230, 245", 0.16, "screen");
    puffs(Math.round(n * 0.4), [-W * 0.05, -H * 0.15, W * 1.05, H * 0.22], [S * 0.06, S * 0.13], "8, 20, 36", 0.5);

    // light rays fanning down to the fields, drawn separately and blurred so their edges are soft
    var rays = document.createElement("canvas");
    rays.width = cv.width; rays.height = cv.height;
    var rx = rays.getContext("2d");
    rx.setTransform(D, 0, 0, D, 0, 0);
    for (var i = 0; i < 16; i++) {
      var ang = Math.PI / 2 + (r() - 0.5) * 1.7, w = 0.015 + r() * 0.04, L = H * (0.5 + r() * 0.45);
      var g = rx.createLinearGradient(cx, cy, cx + Math.cos(ang) * L, cy + Math.sin(ang) * L);
      g.addColorStop(0, "rgba(215, 240, 255, " + (0.1 + r() * 0.14) + ")"); g.addColorStop(1, "rgba(215, 240, 255, 0)");
      rx.fillStyle = g; rx.beginPath(); rx.moveTo(cx, cy);
      rx.lineTo(cx + Math.cos(ang - w) * L, cy + Math.sin(ang - w) * L);
      rx.lineTo(cx + Math.cos(ang + w) * L, cy + Math.sin(ang + w) * L);
      rx.closePath(); rx.fill();
    }
    x.save();
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.globalCompositeOperation = "screen";
    x.filter = "blur(" + Math.round(10 * D) + "px)";
    x.drawImage(rays, 0, 0);
    x.restore();
    // a few lit cloud edges right around the opening
    puffs(Math.round(n * 0.25), [cx - S * 0.24, cy - S * 0.14, cx + S * 0.24, cy + S * 0.03], [S * 0.03, S * 0.075], "225, 242, 252", 0.09, "screen");

    // haze on the horizon, distant tree line, fields
    x.fillStyle = lin(0, H * 0.62, 0, H * 0.76, [[0, "rgba(190, 228, 238, 0)"], [1, "rgba(190, 228, 238, .5)"]]);
    x.fillRect(0, H * 0.6, W, H * 0.18);
    // distant tree line: one continuous, uneven silhouette sitting in the haze
    var base = H * 0.765, v = 0.5;
    x.beginPath(); x.moveTo(0, base + 4);
    for (var tx = 0; tx <= W + 3; tx += 3) {
      var target = 0.45 + 0.3 * Math.sin(tx * 0.011) + 0.2 * Math.sin(tx * 0.047 + 1.3) + (r() - 0.5) * 0.5;
      v = v * 0.65 + target * 0.35;
      x.lineTo(tx, base - H * 0.022 * Math.max(0.08, v));
    }
    x.lineTo(W, base + 4); x.closePath();
    x.fillStyle = "rgba(24, 58, 54, .72)"; x.fill();
    x.fillStyle = lin(0, H * 0.73, 0, H * 0.77, [[0, "rgba(190, 228, 238, 0)"], [1, "rgba(190, 228, 238, .22)"]]);
    x.fillRect(0, H * 0.73, W, H * 0.04);
    hill(H * 0.785, H * 0.025, [[1, 3, 0.4], [0.5, 7, 1]], lin(0, H * 0.76, 0, H * 0.86, [[0, "#4f8a78"], [1, "#2c5f50"]]));
    hill(H * 0.835, H * 0.035, [[1, 2, 2], [0.4, 5, 0]], lin(0, H * 0.8, 0, H, [[0, "#3f7f5c"], [1, "#1f4d36"]]));
    // furrows running towards the light
    x.save(); x.beginPath(); x.rect(0, H * 0.85, W, H * 0.15); x.clip();
    x.strokeStyle = "rgba(8, 36, 24, .25)"; x.lineWidth = 1;
    for (var f = -22; f <= 44; f++) { x.beginPath(); x.moveTo(cx * 0.6, H * 0.82); x.lineTo(cx * 0.6 + (f / 22) * W * 1.6 - W * 0.3, H + 20); x.stroke(); }
    x.restore();
    hill(H * 0.925, H * 0.045, [[1, 1.3, 3.5]], lin(0, H * 0.86, 0, H, [[0, "#2c6a45"], [1, "#123824"]]));

    // keep the left side (headline and text) and the edges darker so text stays easy to read
    x.fillStyle = lin(0, 0, W, 0, [[0, "rgba(4, 16, 30, .55)"], [0.5, "rgba(4, 16, 30, 0)"]]);
    x.fillRect(0, 0, W, H);
    var vg = x.createRadialGradient(W / 2, H * 0.48, S * 0.3, W / 2, H * 0.5, S * 0.8);
    vg.addColorStop(0, "rgba(3, 12, 24, 0)"); vg.addColorStop(1, "rgba(3, 12, 24, .5)");
    x.fillStyle = vg; x.fillRect(0, 0, W, H);
  }

  function start() {
    var cv = document.getElementById("auth-sky");
    if (!cv || !cv.getContext) return;
    function paint() { if (draw(cv) !== false) document.documentElement.classList.add("sky-ready"); }
    paint();
    var t;
    window.addEventListener("resize", function () { clearTimeout(t); t = setTimeout(paint, 150); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
