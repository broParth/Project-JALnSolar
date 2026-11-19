/* Project JAL — opening intro.
   Rain falls, the camera pushes in on one glass drop marked "JAL", follows it down, pulls back as it
   drops into the pond, the pond splashes and ripples, and the brand appears with Sign in and
   Create an account. Whichever the visitor picks opens with a ripple wipe; nothing happens on its own. Plays once per browser session; "/?replay=1" plays it again.
   Every frame is drawn as a pure function of time, so pausing (hidden tab) or a still frame
   (reduced motion) needs no extra state. */
(function () {
  "use strict";

  var root = document.documentElement;
  var LOGIN = root.getAttribute("data-login") || "/login";
  var replay = /[?&]replay\b/.test(location.search);
  var seen = false;
  try { seen = sessionStorage.getItem("jal_intro_seen") === "1"; } catch (e) { /* storage blocked */ }
  if (seen && !replay) { location.replace(LOGIN); return; }
  try { sessionStorage.setItem("jal_intro_seen", "1"); } catch (e) { /* storage blocked */ }

  // Timeline (seconds)
  // The close-up holds long enough to read the tagline; after the splash the scene keeps raining
  // and waits for the visitor to choose Sign in or Create an account (no automatic hand-off).
  // All three tagline lines are on screen together for about 1.3 s before the camera pulls back.
  var T_IN = 1.1, T_IN_END = 2.2, T_OUT = 4.6, T_OUT_END = 5.4, T_IMPACT = 5.65, T_BRAND = 6.2;
  var T_LINES = [2.25, 2.65, 3.1], T_LINES_OFF = 4.45;
  var STILL_T = 7.5;

  // ---------- small helpers ----------
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function ease(x) { x = clamp(x, 0, 1); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function rnd(i) { var x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }
  var TAU = Math.PI * 2;

  document.addEventListener("DOMContentLoaded", start);
  // Coming back with the browser's Back button: don't show a frozen intro, go straight to sign-in.
  window.addEventListener("pageshow", function (e) { if (e.persisted) location.replace(LOGIN); });

  function start() {
    var cv = document.getElementById("intro-canvas");
    var ctx = cv.getContext("2d");
    var reveal = document.getElementById("intro-reveal");
    var brand = document.getElementById("intro-brand");
    var lines = [document.getElementById("il1"), document.getElementById("il2"), document.getElementById("il3")];
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var W, H, R, D, pondY, hx, r0, zMax, offX, offY;
    var sky, pondImg, horizon;
    var layers = [];
    var maxD = 1.5; // drawing resolution cap; lowered automatically on slow machines

    // Glass drops for the rain are drawn once (sharp and out-of-focus) and reused every frame:
    // blurring them live costs far more than a frame allows.
    var SPR = 24, SPAD = 14, dropSharp, dropSoft;
    function buildSprite(soft) {
      var c = document.createElement("canvas");
      c.width = SPR * 2 + SPAD * 2; c.height = Math.ceil(SPR * 3.3 + SPAD * 2);
      var x = c.getContext("2d");
      if (soft) x.filter = "blur(6px)";
      x.translate(c.width / 2, SPAD + SPR * 2.3);
      tearPath(x, SPR);
      var g = x.createRadialGradient(-0.3 * SPR, -0.5 * SPR, SPR * 0.1, 0, -0.2 * SPR, SPR * 1.7);
      g.addColorStop(0, "rgba(255,255,255,.7)"); g.addColorStop(0.5, "rgba(170,220,250,.28)"); g.addColorStop(1, "rgba(40,130,200,.5)");
      x.fillStyle = g; x.fill();
      x.lineWidth = SPR * 0.08; x.strokeStyle = "rgba(255,255,255,.7)"; x.stroke();
      x.beginPath(); x.ellipse(-0.4 * SPR, -0.4 * SPR, 0.14 * SPR, 0.3 * SPR, 0.5, 0, TAU);
      x.fillStyle = "rgba(255,255,255,.85)"; x.fill();
      return c;
    }
    dropSharp = buildSprite(false); dropSoft = buildSprite(true);

    // ---------- sizing and cached layers ----------
    function resize() {
      D = Math.min(maxD, window.devicePixelRatio || 1);
      W = window.innerWidth; H = window.innerHeight; R = Math.min(W, H);
      cv.width = Math.round(W * D); cv.height = Math.round(H * D);
      pondY = H * 0.76; hx = W / 2;
      r0 = R * 0.018;
      zMax = (R * 0.13) / r0;
      var wide = W > 760;
      offX = wide ? W * 0.13 : 0;
      offY = wide ? 0 : H * 0.1;

      sky = document.createElement("canvas");
      sky.width = cv.width; sky.height = cv.height;
      var s = sky.getContext("2d");
      s.setTransform(D, 0, 0, D, 0, 0);
      var g = s.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, "#03162b"); g.addColorStop(0.45, "#0a3a66"); g.addColorStop(0.76, "#1679b4"); g.addColorStop(1, "#38b8bd");
      s.fillStyle = g; s.fillRect(0, 0, W, H);
      var b = s.createRadialGradient(W * 0.8, H * 0.14, 0, W * 0.8, H * 0.14, R * 0.75);
      b.addColorStop(0, "rgba(255, 214, 140, .30)"); b.addColorStop(0.35, "rgba(255, 190, 110, .10)"); b.addColorStop(1, "rgba(255, 190, 110, 0)");
      s.fillStyle = b; s.fillRect(0, 0, W, H);
      var m = s.createRadialGradient(W * 0.3, H * 0.62, 0, W * 0.3, H * 0.62, R * 0.7);
      m.addColorStop(0, "rgba(120, 210, 235, .16)"); m.addColorStop(1, "rgba(120, 210, 235, 0)");
      s.fillStyle = m; s.fillRect(0, 0, W, H);

      // The pond (water colour plus a faint reflection of the sky) is drawn once.
      var ph = H - pondY + 4;
      pondImg = document.createElement("canvas");
      pondImg.width = cv.width; pondImg.height = Math.ceil(ph * D);
      var p = pondImg.getContext("2d");
      p.setTransform(D, 0, 0, D, 0, 0);
      var pf = p.createLinearGradient(0, 0, 0, H * 0.3);
      pf.addColorStop(0, "#0e5a8f"); pf.addColorStop(1, "#03203b");
      p.fillStyle = pf; p.fillRect(0, 0, W, ph);
      p.globalAlpha = 0.3; p.setTransform(D, 0, 0, -D, 0, pondY * D); p.drawImage(sky, 0, 0, W, H);

      horizon = ctx.createLinearGradient(0, 0, W, 0);
      horizon.addColorStop(0, "rgba(190, 232, 255, 0)"); horizon.addColorStop(0.5, "rgba(190, 232, 255, .6)"); horizon.addColorStop(1, "rgba(190, 232, 255, 0)");

      // Rain: far and mid streaks, near glass drops. depth = how strongly each layer follows the camera zoom.
      layers = [
        { kind: "streak", n: Math.round(W / 8), depth: 0.12, sp: [1.1, 1.6], len: [0.025, 0.045], w: 1, a: 0.22, seed: 1 },
        { kind: "streak", n: Math.round(W / 16), depth: 0.45, sp: [1.7, 2.3], len: [0.05, 0.08], w: 1.5, a: 0.34, seed: 2 },
        { kind: "drop", n: Math.max(8, Math.round(W / 90)), depth: 1, sp: [0.32, 0.5], r: [0.006, 0.011], a: 0.9, seed: 3 }
      ];
    }

    // ---------- shapes ----------
    function tearPath(c, r) {
      c.beginPath();
      c.moveTo(0, -2.3 * r);
      c.bezierCurveTo(0.5 * r, -1.5 * r, r, -0.85 * r, r, 0);
      c.arc(0, 0, r, 0, Math.PI, false);
      c.bezierCurveTo(-r, -0.85 * r, -0.5 * r, -1.5 * r, 0, -2.3 * r);
      c.closePath();
    }

    // soft: 0 = in focus, 1 = fully out of focus (cross-fades the two pre-drawn images)
    function smallDrop(x, y, r, alpha, soft) {
      var s = r / SPR, w = dropSharp.width * s, h = dropSharp.height * s;
      var ox = x - w / 2, oy = y - (SPAD + SPR * 2.3) * s;
      if (soft < 1) { ctx.globalAlpha = alpha * (1 - soft); ctx.drawImage(dropSharp, ox, oy, w, h); }
      if (soft > 0) { ctx.globalAlpha = alpha * soft; ctx.drawImage(dropSoft, ox, oy, w, h); }
      ctx.globalAlpha = 1;
    }

    function droplet(x, y, r, alpha) {
      var g = ctx.createRadialGradient(x - r * 0.3, y - r * 0.3, 0, x, y, r);
      g.addColorStop(0, "rgba(255,255,255," + 0.95 * alpha + ")"); g.addColorStop(1, "rgba(150,215,250," + 0.55 * alpha + ")");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
    }

    // The hero drop: a real lens. It shows a small, upside-down copy of the sky, darkens at the edge,
    // focuses light into a bright patch near the bottom and carries the wordmark.
    function hero(x, y, r, wob, label, z) {
      ctx.save();
      ctx.translate(x, y); ctx.scale(1 + wob, 1 - wob * 0.6);

      ctx.save(); tearPath(ctx, r);
      ctx.shadowColor = "rgba(120, 205, 255, .55)"; ctx.shadowBlur = Math.min(60, r * z * D * 0.8);
      ctx.fillStyle = "rgba(110, 190, 240, .3)"; ctx.fill();
      ctx.restore();

      ctx.save(); tearPath(ctx, r); ctx.clip();
      // fill the whole drop with an upside-down, squeezed copy of the sky
      ctx.save(); ctx.translate(0, -0.65 * r); ctx.scale((2.7 * r) / W, (-3.7 * r) / H);
      ctx.drawImage(sky, -W / 2, -H / 2, W, H); ctx.restore();
      ctx.fillStyle = "rgba(90, 180, 240, .16)"; ctx.fillRect(-2 * r, -2.5 * r, 4 * r, 4 * r);
      var fr = ctx.createRadialGradient(0, -0.25 * r, r * 0.25, 0, -0.3 * r, r * 1.6);
      fr.addColorStop(0, "rgba(255,255,255,0)"); fr.addColorStop(0.68, "rgba(8,55,105,.18)"); fr.addColorStop(1, "rgba(2,22,46,.7)");
      ctx.fillStyle = fr; ctx.fillRect(-2 * r, -2.5 * r, 4 * r, 4 * r);
      var ca = ctx.createRadialGradient(r * 0.12, r * 0.6, 0, r * 0.12, r * 0.6, r * 0.62);
      ca.addColorStop(0, "rgba(255,255,255,.6)"); ca.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = ca; ctx.beginPath(); ctx.ellipse(r * 0.1, r * 0.6, r * 0.62, r * 0.3, 0, 0, TAU); ctx.fill();
      ctx.restore();

      tearPath(ctx, r);
      var rim = ctx.createLinearGradient(-r, -2.3 * r, r, r);
      rim.addColorStop(0, "rgba(255,255,255,.95)"); rim.addColorStop(0.55, "rgba(255,255,255,.35)"); rim.addColorStop(1, "rgba(255,255,255,.7)");
      ctx.lineWidth = r * 0.045; ctx.strokeStyle = rim; ctx.stroke();

      var sp = ctx.createRadialGradient(-0.4 * r, -0.45 * r, 0, -0.4 * r, -0.45 * r, r * 0.34);
      sp.addColorStop(0, "rgba(255,255,255,.95)"); sp.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = sp; ctx.beginPath(); ctx.ellipse(-0.4 * r, -0.45 * r, 0.14 * r, 0.34 * r, 0.5, 0, TAU); ctx.fill();
      ctx.fillStyle = "rgba(255,255,255,.8)"; ctx.beginPath(); ctx.arc(-0.12 * r, -1.25 * r, 0.05 * r, 0, TAU); ctx.fill();
      ctx.beginPath(); ctx.arc(0, 0, 0.8 * r, 0.25, 1.3);
      ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = r * 0.05; ctx.lineCap = "round"; ctx.stroke();

      if (label > 0.01) {
        ctx.globalAlpha = label;
        ctx.font = "800 " + (r * 0.6) + "px Manrope, 'DM Sans', sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.shadowColor = "rgba(2, 30, 60, .5)"; ctx.shadowBlur = r * 0.18 * z * D;
        ctx.fillStyle = "#fff"; ctx.fillText("JAL", 0, r * 0.06);
      }
      ctx.restore();
    }

    function ring(x, y, rx, a, lw) {
      if (rx <= 0.5 || a <= 0.005) return;
      ctx.lineWidth = lw;
      ctx.beginPath(); ctx.ellipse(x, y, rx, rx * 0.17, 0, 0, TAU);
      ctx.strokeStyle = "rgba(225, 244, 255," + a + ")"; ctx.stroke();
      ctx.beginPath(); ctx.ellipse(x, y + rx * 0.014, rx * 0.93, rx * 0.155, 0, 0, TAU);
      ctx.strokeStyle = "rgba(2, 26, 50," + a * 0.55 + ")"; ctx.stroke();
    }

    // ---------- the scene at time t ----------
    function heroY(t) {
      var y1 = H * 0.27, y2 = y1 + H * 0.035 * (T_OUT - T_IN);
      if (t < T_IN) return lerp(H * 0.03, y1, t / T_IN);
      if (t < T_OUT) return y1 + H * 0.035 * (t - T_IN);
      var p = clamp((t - T_OUT) / (T_IMPACT - T_OUT), 0, 1);
      return y2 + (pondY - r0 - y2) * p * p;
    }

    function render(t) {
      var k = ease((t - T_IN) / (T_IN_END - T_IN)) * (1 - ease((t - T_OUT) / (T_OUT_END - T_OUT)));
      var z = 1 + (zMax - 1) * k;
      var hy = heroY(t);
      var fx = hx + offX / zMax, fy = hy - 0.6 * r0 + offY / zMax;
      var camX = lerp(W / 2, fx, k), camY = lerp(H / 2, fy, k);

      ctx.setTransform(D, 0, 0, D, 0, 0);
      ctx.globalAlpha = 1; ctx.filter = "none";
      ctx.drawImage(sky, 0, 0, W, H);

      function camera(depth) {
        var zl = 1 + (z - 1) * depth;
        ctx.setTransform(D, 0, 0, D, 0, 0);
        ctx.translate(W / 2, H / 2); ctx.scale(zl, zl); ctx.translate(-camX, -camY);
        return zl;
      }

      // pond (follows the camera fully)
      camera(1);
      ctx.drawImage(pondImg, 0, pondY, W, pondImg.height / D);
      ctx.fillStyle = horizon; ctx.fillRect(0, pondY - 1, W, 2);
      ctx.strokeStyle = "rgba(200, 236, 255, .16)"; ctx.lineWidth = 1; ctx.beginPath();
      for (var i = 0; i < 34; i++) {
        var dy = Math.pow(rnd(i + 50), 2) * H * 0.22, len = (8 + rnd(i + 70) * 34) * (1 + dy / (H * 0.08));
        var sx = rnd(i) * W + Math.sin(t * 0.7 + i) * W * 0.01;
        ctx.moveTo(sx, pondY + 3 + dy); ctx.lineTo(sx + len, pondY + 3 + dy);
      }
      ctx.stroke();

      // ripples: rain hitting the pond, then the big impact rings
      for (var j = Math.floor((t - 1.2) / 0.13); j <= Math.floor(t / 0.13); j++) {
        if (j < 0) continue;
        var p = (t - j * 0.13) / 1.2;
        if (p < 0 || p > 1) continue;
        ring(rnd(j + 900) * W, pondY + 4 + Math.pow(rnd(j + 950), 2) * H * 0.18, R * 0.035 * Math.sqrt(p), 0.5 * Math.pow(1 - p, 1.5) * (1 - k), 1);
      }
      var pd = 0;
      if (t >= T_IMPACT) {
        for (var n = 0; n < 5; n++) {
          var q = (t - T_IMPACT - n * 0.17) / 2.8;
          if (q > 0 && q < 1) ring(hx, pondY + 2, R * (0.34 + n * 0.05) * Math.pow(q, 0.62), 0.85 * Math.pow(1 - q, 1.4), lerp(2.4, 0.8, q));
        }
        // the jet's falling droplet leaves one more ring
        var v = R * 0.55, gj = R * 2.4, h0 = R * 0.13;
        pd = (v + Math.sqrt(v * v + 2 * gj * h0)) / gj;
        var q2 = (t - (T_IMPACT + 0.12 + 0.31 + pd)) / 1.6;
        if (q2 > 0 && q2 < 1) ring(hx, pondY + 2, R * 0.12 * Math.sqrt(q2), 0.7 * (1 - q2), 1.2);
      }

      // rain streaks (far, mid)
      for (var l = 0; l < 2; l++) {
        var L = layers[l], zl = camera(L.depth), span = pondY + H * 0.2;
        ctx.globalAlpha = L.a * (1 - 0.6 * k);
        ctx.strokeStyle = "rgba(205, 235, 255, 1)"; ctx.lineWidth = L.w; ctx.lineCap = "round";
        ctx.beginPath();
        for (var a = 0; a < L.n; a++) {
          var id = L.seed * 1000 + a;
          var sp2 = H * lerp(L.sp[0], L.sp[1], rnd(id + 1)), ln = H * lerp(L.len[0], L.len[1], rnd(id + 2));
          var y = -H * 0.2 + ((rnd(id + 3) * span + sp2 * t) % span);
          var x = rnd(id) * W * 1.3 - W * 0.15;
          if (y - ln > pondY) continue;
          ctx.moveTo(x, Math.min(y, pondY)); ctx.lineTo(x + ln * 0.12, y - ln);
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      // near glass drops, thrown out of focus while the camera is close
      var N = layers[2];
      camera(N.depth);
      var soft = clamp(k * 1.6, 0, 1);
      for (var c = 0; c < N.n; c++) {
        var id2 = N.seed * 1000 + c, span2 = pondY + H * 0.25;
        var yy = -H * 0.25 + ((rnd(id2 + 3) * span2 + H * lerp(N.sp[0], N.sp[1], rnd(id2 + 1)) * t) % span2);
        var xx = rnd(id2) * W;
        if (Math.abs(xx - hx) < W * 0.08) xx += W * 0.16;
        if (yy < pondY - R * 0.01) smallDrop(xx, yy, R * lerp(N.r[0], N.r[1], rnd(id2 + 2)), N.a * (1 - 0.55 * k), soft);
      }

      // the hero drop
      camera(1);
      if (t < T_IMPACT) hero(hx, hy, r0, 0.05 * Math.sin(t * 9) * k + 0.012 * Math.sin(t * 5), k, z);

      // splash
      if (t >= T_IMPACT) {
        var ps = t - T_IMPACT, g = R * 2.4;
        if (ps < 0.5) {
          var fl = ctx.createRadialGradient(hx, pondY, 0, hx, pondY, R * 0.14 * (0.5 + ps * 2));
          fl.addColorStop(0, "rgba(220, 245, 255," + 0.6 * (1 - ps / 0.5) + ")"); fl.addColorStop(1, "rgba(220, 245, 255, 0)");
          ctx.fillStyle = fl; ctx.beginPath(); ctx.ellipse(hx, pondY, R * 0.3, R * 0.08, 0, 0, TAU); ctx.fill();
        }
        if (ps < 1.3) {
          var mi = ctx.createRadialGradient(hx, pondY - R * 0.03, 0, hx, pondY - R * 0.03, R * (0.05 + 0.16 * ps));
          mi.addColorStop(0, "rgba(255,255,255," + 0.12 * (1 - ps / 1.3) + ")"); mi.addColorStop(1, "rgba(255,255,255,0)");
          ctx.fillStyle = mi; ctx.fillRect(hx - R * 0.4, pondY - R * 0.4, R * 0.8, R * 0.5);
        }
        // crown of droplets
        for (var cr = 0; cr < 28; cr++) {
          var ang = (TAU * cr) / 28 + rnd(cr + 600) * 0.2;
          var out = R * (0.09 + 0.1 * rnd(cr + 610)), up = R * (0.5 + 0.45 * rnd(cr + 620));
          var hgt = up * ps - 0.5 * g * ps * ps;
          if (hgt < -R * 0.002) continue;
          var dist = r0 * 0.9 + out * ps;
          droplet(hx + Math.cos(ang) * dist, pondY + Math.sin(ang) * dist * 0.2 - hgt,
            r0 * (0.16 + 0.2 * rnd(cr + 630)) * (1 - 0.35 * clamp(ps, 0, 1)), 1);
        }
        // central jet, which pinches off one droplet at its peak
        var pj = ps - 0.12;
        if (pj > 0 && pj < 0.62) {
          var jh = R * 0.13 * Math.sin((Math.PI * pj) / 0.62), jw = r0 * 0.85 * (1 - 0.3 * pj);
          var jg = ctx.createLinearGradient(0, pondY - jh, 0, pondY);
          jg.addColorStop(0, "rgba(235, 248, 255, .95)"); jg.addColorStop(1, "rgba(120, 200, 250, .25)");
          ctx.fillStyle = jg; ctx.beginPath();
          ctx.moveTo(hx - jw, pondY); ctx.lineTo(hx - jw * 0.55, pondY - jh + jw * 0.5);
          ctx.arc(hx, pondY - jh + jw * 0.5, jw * 0.55, Math.PI, 0); ctx.lineTo(hx + jw, pondY); ctx.closePath(); ctx.fill();
        }
        var pp = pj - 0.31;
        if (pp > 0 && pp < pd) droplet(hx, pondY - (R * 0.13 + R * 0.55 * pp - 0.5 * g * pp * pp), r0 * 0.5, 1);
      }

      // speed streaks in screen space while the camera follows the drop
      ctx.setTransform(D, 0, 0, D, 0, 0);
      if (k > 0.02) {
        ctx.strokeStyle = "rgba(220, 242, 255," + 0.3 * k + ")"; ctx.lineWidth = 1.2; ctx.beginPath();
        for (var s = 0; s < 24; s++) {
          var sl = H * (0.06 + 0.1 * rnd(s + 400)), sy = H + sl - ((t * H * 1.7 + rnd(s + 410) * (H + sl)) % (H + sl));
          var sx2 = rnd(s + 420) * W;
          ctx.moveTo(sx2, sy); ctx.lineTo(sx2, sy + sl);
        }
        ctx.stroke();
      }
    }

    // ---------- page text and leaving ----------
    var leaving = false;
    function dom(t) {
      for (var i = 0; i < 3; i++) lines[i].classList.toggle("show", t > T_LINES[i] && t < T_LINES_OFF);
      if (t >= T_BRAND && !brand.classList.contains("show")) {
        brand.classList.add("show");
        skip.classList.add("gone");
        // Put keyboard focus on "Sign in" so Enter works (the Skip button it may have been on is now hidden).
        var a = document.activeElement;
        if (!a || a === document.body || a === skip) enter.focus({ preventScroll: true });
      }
    }

    function go(href) {
      if (leaving) return;
      leaving = true;
      if (reduce) { location.assign(href); return; }
      reveal.style.setProperty("--x", (hx / W) * 100 + "%");
      reveal.style.setProperty("--y", (pondY / H) * 100 + "%");
      reveal.classList.add("go");
      setTimeout(function () { location.assign(href); }, 720);
    }

    function hookLink(el) {
      if (!el) return;
      el.addEventListener("click", function (e) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault(); go(el.getAttribute("href"));
      });
    }
    var enter = document.getElementById("intro-enter");
    var skip = document.getElementById("intro-skip");
    hookLink(enter);
    hookLink(document.querySelector(".intro-link"));
    // "Skip intro" and Escape jump to the splash and the Sign in / Create account choice;
    // they never choose for the visitor. (Without JavaScript the link simply opens sign-in.)
    function skipToChoice() { if (t < T_BRAND) t = Math.max(t, T_IMPACT + 0.25); }
    skip.addEventListener("click", function (e) { e.preventDefault(); skipToChoice(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") skipToChoice(); });

    // ---------- run ----------
    var t = 0, last = null, slow = 0;
    function tick(now) {
      if (last === null) last = now;
      var dt = (now - last) / 1000; last = now;
      t += Math.min(0.1, dt); // keeps real speed down to ~10 fps; pauses in hidden tabs
      // If drawing takes too long on this machine, drop to a lower resolution once.
      var t0 = performance.now();
      render(t); dom(t);
      if (performance.now() - t0 > 22) { if (++slow > 8 && maxD > 1) { maxD = 1; resize(); } } else slow = Math.max(0, slow - 1);
      requestAnimationFrame(tick);
    }

    resize();
    window.addEventListener("resize", function () { resize(); if (reduce) render(STILL_T); });

    if (reduce) {
      // A still frame of the pond after the splash, with the brand and buttons. Nothing moves or auto-advances.
      render(STILL_T); brand.classList.add("show"); skip.classList.add("gone");
      return;
    }
    render(0);
    var fontsReady = document.fonts && document.fonts.load ? document.fonts.load("800 40px Manrope") : Promise.resolve();
    Promise.race([fontsReady, new Promise(function (r) { setTimeout(r, 700); })])
      .then(function () { requestAnimationFrame(tick); }, function () { requestAnimationFrame(tick); });
  }
})();
