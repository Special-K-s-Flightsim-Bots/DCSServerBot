/*
 * Login page background: a starfield, aircraft silhouettes, contrails and missile trails drawn on
 * #dcs-tactical-canvas at about 30 frames per second. Frames are skipped while the document is
 * hidden or a key is being typed, and the palette follows data-theme (dark / light).
 */
(function () {
  const canvas = document.getElementById('dcs-tactical-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let W = (canvas.width = window.innerWidth);
  let H = (canvas.height = window.innerHeight);

  window.addEventListener('resize', function () {
    W = canvas.width = window.innerWidth;
    H = canvas.height = window.innerHeight;
  });

  const stars = Array.from({ length: 130 }, function () {
    return {
      x: Math.random(),
      y: Math.random() * 0.72,
      r: Math.random() * 1.5 + 0.4,
      alpha: Math.random() * 0.7 + 0.2,
      speed: Math.random() * 0.02 + 0.006,
      phase: Math.random() * Math.PI * 2
    };
  });

  const fleetSpecs = [
    { callsign: 'VIPER-1 • F-16C', sub: 'FL280 • M0.95', color: '#38bdf8', glow: 'rgba(56,189,248,0.85)', x: 0.08, y: 0.18, vx: 0.52, vy: -0.08, turn: 0.0005, scale: 1.05 },
    { callsign: 'VIPER-2 • WING',  sub: 'FL280 • TRAIL', color: '#38bdf8', glow: 'rgba(56,189,248,0.85)', x: 0.04, y: 0.21, vx: 0.52, vy: -0.08, turn: 0.0005, scale: 0.92 },
    { callsign: 'HORNET-1 • CAP',  sub: 'FL250 • M0.91', color: '#34d399', glow: 'rgba(52,211,153,0.85)', x: 0.88, y: 0.24, vx: -0.46, vy: 0.07, turn: -0.0004, scale: 1.0 },
    { callsign: 'EAGLE-1 • LEAD',  sub: 'FL310 • M1.14', color: '#fbbf24', glow: 'rgba(251,191,36,0.9)',  x: 0.18, y: 0.62, vx: 0.62, vy: -0.14, turn: -0.0006, scale: 1.08 },
    { callsign: 'FALCON-2 • SEAD', sub: 'FL220 • AGM88', color: '#38bdf8', glow: 'rgba(56,189,248,0.85)', x: 0.78, y: 0.14, vx: -0.50, vy: 0.12, turn: 0.0005, scale: 0.95 },
    { callsign: 'BOGEY • SU-35S',  sub: 'HOSTILE • M1.2', color: '#f87171', glow: 'rgba(248,113,113,0.9)', x: 0.92, y: 0.58, vx: -0.58, vy: -0.15, turn: 0.0007, scale: 1.05, hostile: true },
    { callsign: 'BOGEY-2 • MIG31', sub: 'BRAA 270/38',   color: '#f87171', glow: 'rgba(248,113,113,0.9)', x: 0.96, y: 0.62, vx: -0.56, vy: -0.14, turn: 0.0007, scale: 0.94, hostile: true },
    { callsign: 'MAGIC • AWACS',   sub: 'E-3A • FL360',  color: '#a78bfa', glow: 'rgba(167,139,250,0.85)', x: 0.25, y: 0.11, vx: 0.30, vy: 0.02, turn: 0.0002, scale: 1.12, awacs: true },
    { callsign: 'STRIKE-1 • CAP',  sub: 'FL260 • FOX-3', color: '#34d399', glow: 'rgba(52,211,153,0.85)', x: 0.12, y: 0.44, vx: 0.48, vy: 0.09, turn: -0.0004, scale: 0.96 },
    { callsign: 'TEXACO • TANKER', sub: 'KC-135 • FL200',color: '#fbbf24', glow: 'rgba(251,191,36,0.85)', x: 0.68, y: 0.40, vx: -0.34, vy: -0.04, turn: 0.0003, scale: 1.0 }
  ];

  const jets = fleetSpecs.map(function (s) {
    return Object.assign({}, s, {
      px: s.x * W,
      py: s.y * H,
      angle: Math.atan2(s.vy, s.vx),
      speed: Math.hypot(s.vx, s.vy),
      trail: []
    });
  });

  const missiles = [];
  function spawnMissile() {
    const friendly = jets.filter(function (j) { return !j.hostile && !j.awacs; });
    const bogeys = jets.filter(function (j) { return j.hostile; });
    if (!friendly.length || !bogeys.length) return;
    const shooter = friendly[Math.floor(Math.random() * friendly.length)];
    const target = bogeys[Math.floor(Math.random() * bogeys.length)];
    missiles.push({
      x: shooter.px,
      y: shooter.py,
      target: target,
      life: 0,
      maxLife: 190,
      trail: []
    });
  }
  setInterval(spawnMissile, 4200);
  setTimeout(spawnMissile, 1200);

  function drawJetShape(ctx, scale, color, flame, isAwacs) {
    ctx.save();
    ctx.scale(scale, scale);

    ctx.fillStyle = flame;
    ctx.beginPath();
    ctx.moveTo(-5, -1.8);
    ctx.lineTo(-17, 0);
    ctx.lineTo(-5, 1.8);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(11, 0);
    ctx.lineTo(3, 2.2);
    ctx.lineTo(-2, 8.5);
    ctx.lineTo(-5.5, 8.2);
    ctx.lineTo(-3.5, 2.4);
    ctx.lineTo(-8.5, 4.8);
    ctx.lineTo(-10, 4.2);
    ctx.lineTo(-7.5, 0);
    ctx.lineTo(-10, -4.2);
    ctx.lineTo(-8.5, -4.8);
    ctx.lineTo(-3.5, -2.4);
    ctx.lineTo(-5.5, -8.2);
    ctx.lineTo(-2, -8.5);
    ctx.lineTo(3, -2.2);
    ctx.closePath();
    ctx.fill();

    if (isAwacs) {
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      ctx.arc(-2, 0, 5.2, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }

  // The drawing follows the console's theme: white stars and dark label boxes are dirt and
  // holes on a pale sky, so the light theme asks for INK -- darker strokes, solid white tags.
  const INK = {
    dark: {
      star: 'rgba(224, 242, 254, 0.55)',
      trailHostile: 'rgba(248, 113, 113, 0.38)',
      trailFriendly: 'rgba(125, 211, 252, 0.38)',
      awacs: '167, 139, 250',
      tagBg: 'rgba(4, 11, 25, 0.85)',
      tagHostile: 'rgba(248, 113, 113, 0.45)',
      tagFriendly: 'rgba(56, 189, 248, 0.35)',
      tagText: 'rgba(203, 213, 225, 0.85)',
      missile: 'rgba(251, 191, 36, 0.75)'
    },
    light: {
      star: 'rgba(51, 65, 85, 0.38)',
      trailHostile: 'rgba(190, 18, 60, 0.40)',
      trailFriendly: 'rgba(3, 105, 161, 0.40)',
      awacs: '109, 40, 217',
      tagBg: 'rgba(255, 255, 255, 0.92)',
      tagHostile: 'rgba(190, 18, 60, 0.45)',
      tagFriendly: 'rgba(3, 105, 161, 0.45)',
      tagText: 'rgba(15, 23, 42, 0.85)',
      missile: 'rgba(180, 83, 9, 0.75)'
    }
  };
  // The jets wear bright colours that read on a night sky and vanish on a pale one, so the
  // light theme maps each silhouette to its ink equivalent.
  const INK_OF = {
    '#38bdf8': '#0369a1', '#34d399': '#047857', '#fbbf24': '#b45309',
    '#f87171': '#be123c', '#a78bfa': '#6d28d9'
  };
  let ink = INK.dark;

  let tick = 0;
  let lastFrame = 0;
  let isTyping = false;
  let typingTimer = null;
  document.addEventListener('keydown', function () {
    isTyping = true;
    clearTimeout(typingTimer);
    typingTimer = setTimeout(function () { isTyping = false; }, 180);
  }, { passive: true });

  function animate(now) {
    requestAnimationFrame(animate);
    if (document.hidden || isTyping) return;
    if (now - lastFrame < 33) return;
    lastFrame = now;
    tick++;
    ink = document.documentElement.getAttribute('data-theme') === 'light' ? INK.light : INK.dark;
    ctx.clearRect(0, 0, W, H);

    ctx.fillStyle = ink.star;
    ctx.beginPath();
    for (let sIdx = 0; sIdx < stars.length; sIdx++) {
      const st = stars[sIdx];
      ctx.moveTo(st.x * W + st.r, st.y * H);
      ctx.arc(st.x * W, st.y * H, st.r, 0, Math.PI * 2);
    }
    ctx.fill();

    for (let jIdx = 0; jIdx < jets.length; jIdx++) {
      const j = jets[jIdx];
      j.angle += Math.sin(tick * 0.008 + j.x * 10) * j.turn;
      j.px += Math.cos(j.angle) * j.speed * 1.4;
      j.py += Math.sin(j.angle) * j.speed * 1.4;

      const margin = 90;
      if (j.px > W + margin) { j.px = -margin; j.trail.length = 0; }
      if (j.px < -margin) { j.px = W + margin; j.trail.length = 0; }
      if (j.py > H * 0.76) {
        j.angle = -Math.abs(j.angle) * 0.6 - 0.15;
      }
      if (j.py < 70) {
        j.angle = Math.abs(j.angle) * 0.6 + 0.15;
      }

      j.trail.push({ x: j.px, y: j.py });
      if (j.trail.length > 36) j.trail.shift();

      if (j.trail.length > 2) {
        ctx.strokeStyle = j.hostile ? ink.trailHostile : ink.trailFriendly;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.moveTo(j.trail[0].x, j.trail[0].y);
        for (let i = 1; i < j.trail.length; i++) {
          ctx.lineTo(j.trail[i].x, j.trail[i].y);
        }
        ctx.stroke();
      }

      if (j.awacs) {
        const pulseR = (tick % 45) * 1.6;
        const pulseA = Math.max(0, 1 - pulseR / 72) * 0.35;
        ctx.strokeStyle = 'rgba(' + ink.awacs + ', ' + pulseA.toFixed(2) + ')';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(j.px, j.py, pulseR, 0, Math.PI * 2);
        ctx.stroke();
      }

      const col = ink === INK.light ? (INK_OF[j.color] || j.color) : j.color;
      const flame = ink === INK.light ? 'rgba(180, 83, 9, 0.8)' : 'rgba(251, 191, 36, 0.8)';

      ctx.save();
      ctx.translate(j.px, j.py);
      ctx.rotate(j.angle);
      drawJetShape(ctx, j.scale, col, flame, j.awacs);
      ctx.restore();

      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.strokeRect(j.px - 9, j.py - 9, 18, 18);

      const tagX = j.px + 14;
      const tagY = j.py - 22;
      ctx.fillStyle = ink.tagBg;
      ctx.fillRect(tagX, tagY, 94, 20);
      ctx.strokeStyle = j.hostile ? ink.tagHostile : ink.tagFriendly;
      ctx.strokeRect(tagX, tagY, 94, 20);

      ctx.font = 'bold 8.5px Consolas, monospace';
      ctx.fillStyle = col;
      ctx.fillText(j.callsign, tagX + 4, tagY + 9);
      ctx.font = '7.5px Consolas, monospace';
      ctx.fillStyle = ink.tagText;
      ctx.fillText(j.sub, tagX + 4, tagY + 17);
    }

    for (let i = missiles.length - 1; i >= 0; i--) {
      const m = missiles[i];
      m.life++;
      const dx = m.target.px - m.x;
      const dy = m.target.py - m.y;
      const dist = Math.hypot(dx, dy);
      if (dist < 14 || m.life > m.maxLife || dist > W * 0.7) {
        missiles.splice(i, 1);
        continue;
      }
      const speed = 3.2;
      m.x += (dx / dist) * speed;
      m.y += (dy / dist) * speed;
      m.trail.push({ x: m.x, y: m.y });
      if (m.trail.length > 16) m.trail.shift();

      if (m.trail.length > 1) {
        ctx.strokeStyle = ink.missile;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(m.trail[0].x, m.trail[0].y);
        for (let k = 1; k < m.trail.length; k++) {
          ctx.lineTo(m.trail[k].x, m.trail[k].y);
        }
        ctx.stroke();
      }
      ctx.fillStyle = '#fef08a';
      ctx.fillRect(m.x - 1.5, m.y - 1.5, 3, 3);
    }
  }

  requestAnimationFrame(animate);
})();
