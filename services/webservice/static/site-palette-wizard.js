/* site-palette-wizard.js — the palette wizard: a starting point in, six steps and the twenty-three
 * declarations of an expert sheet out.
 *
 * THE MATHS. OKLCH: a base colour gives the hue and the chroma floor, lightness is the ladder, and
 * each declaration is emitted as the hex the shipped sheets carry. Every pair the rule names is
 * measured as an sRGB relative-luminance ratio against the ground it lands on; a pair below its
 * role's minimum is MOVED — the token's lightness steps away from its ground in 0.01 increments until
 * the pair clears — and the move is REPORTED in the panel, never clamped in silence. One ladder per
 * scheme: a light page runs the L values the other way and inverts the accent trio, because on a
 * light page "more emphatic" means darker.
 *
 * WHERE IT WRITES. Nowhere but the expert textarea already on the page: the handoff dispatches
 * `sc-sheet` on the expert block, which is that editor's own setter. The card's Save remains the only
 * write and the server's validator the only refusal, so this file decides no rule the save does not.
 *
 * THE BLOCK'S PANELS. The wizard is two PANELS of the palette block — the gallery at rest and the
 * door (`[data-pal-start]`) is the only thing that shows them; `← Presets` puts the gallery back. The
 * card's OWN frame is repainted by `paintPreview`, never reloaded — the frame is resolved from the
 * wizard's form, never from the first frame on the page.
 *
 * THE PREVIEW is the card's own iframe — the site's own document — with the candidate values set on
 * its root as inline custom properties and its `data-theme` set to the scheme they were derived for.
 * Nothing here draws the site, and no request is made: the document is the one already loaded.
 */
(() => {
  'use strict';

  /* ---------------------------------------------------------------- sRGB <-> OKLab (Ottosson) */
  const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const toSrgb = (c) => {
    const v = Math.min(Math.max(c, 0), 1);
    return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
  };

  const hexRgb = (value) => {
    let hex = String(value || '').trim().replace(/^#/, '');
    if (hex.length === 3) {
      hex = hex.split('').map((ch) => ch + ch).join('');
    }
    if (!/^[0-9a-f]{6}$/i.test(hex)) {
      return null;
    }
    return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  };

  /* one token's value as an sRGB triple: the shipped tokens are hex or `rgb()`/`rgba()`, and a value
   * that is neither (a gradient) has no colour to measure — it answers null and is reported as carried */
  const colourRgb = (value) => {
    const text = String(value || '').trim();
    const hex = hexRgb(text);
    if (hex) {
      return hex;
    }
    const parts = text.match(/^rgba?\(([^)]+)\)$/i);
    if (!parts) {
      return null;
    }
    const nums = parts[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    if (nums.length < 3 || nums.slice(0, 3).some((n) => Number.isNaN(n))) {
      return null;
    }
    return nums.slice(0, 3).map((n) => n / 255);
  };

  const rgbHex = (rgb) => {
    if (rgb.length === 4) {
      const [r, g, b, a] = rgb;
      return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${a})`;
    }
    return '#' + rgb.map((c) => Math.round(Math.min(Math.max(c, 0), 1) * 255)
      .toString(16).padStart(2, '0')).join('');
  };

  const oklab = (rgb) => {
    const [r, g, b] = rgb.map(toLinear);
    const [l, m, s] = [
      0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
      0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
      0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
    ].map((x) => Math.sign(x) * Math.abs(x) ** (1 / 3));
    return [
      0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
      1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
      0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    ];
  };

  const fromOklab = ([L, a, b]) => {
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
    return [
      toSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
      toSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
      toSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
    ];
  };

  const oklch = (rgb) => {
    const [L, a, b] = oklab(rgb);
    return [L, Math.hypot(a, b), ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360];
  };

  /* the nearest in-gamut sRGB for (L, C, h): chroma is reduced until the colour fits */
  const fromOklch = (L, C, h) => {
    const rad = (h * Math.PI) / 180;
    for (let i = 0; i <= 100; i += 1) {
      const chroma = i === 0 ? C : C * (1 - i / 100);
      const rgb = fromOklab([L, chroma * Math.cos(rad), chroma * Math.sin(rad)]);
      if (rgb.every((c) => c >= 0.0005 && c <= 0.9995)) {
        return rgb;
      }
    }
    return fromOklab([L, 0, 0]);
  };

  /* ---------------------------------------------------------------- WCAG contrast */
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map(toLinear);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a, b) => {
    const la = luminance(a);
    const lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };

  /* ---------------------------------------------------------------- the ladder */
  /* One row per step. `tokens` is what the step declares, `pair` is the pair the step's chip measures,
   * so the sheet's shape and the picture of it cannot disagree. */
  const STEPS = [
    {id: 'ground', tokens: ['--ground', '--stage'], pair: ['--brass', '--ground']},
    {id: 'panel', tokens: ['--panel', '--panel-2', '--band'], pair: ['--fg', '--panel']},
    {id: 'line', tokens: ['--line', '--line-2'], pair: ['--line-2', '--panel']},
    {id: 'accent', tokens: ['--brass', '--brass-hi', '--brass-dim'], pair: ['--brass', '--panel']},
    {id: 'text', tokens: ['--fg'], pair: ['--fg', '--ground']},
    {id: 'steps', tokens: ['--fg-2', '--fg-3'], pair: ['--fg-3', '--ground']},
  ];

  const DARK = {
    ground: [['--ground', 0.170, 0.016], ['--stage', 0.130, 0.014]],
    panel: [['--panel', 0.215, 0.020], ['--panel-2', 0.245, 0.022], ['--band', 0.200, 0.020]],
    line: [['--line', 0.340, 0.026], ['--line-2', 0.440, 0.030]],
    text: [['--fg', 0.970, 0.012]],
    steps: [['--fg-2', 0.855, 0.016], ['--fg-3', 0.700, 0.020]],
  };

  const LIGHT = {
    ground: [['--ground', 0.960, 0.008], ['--stage', 0.935, 0.010]],
    panel: [['--panel', 0.995, 0.004], ['--panel-2', 0.975, 0.008], ['--band', 0.915, 0.012]],
    line: [['--line', 0.880, 0.014], ['--line-2', 0.780, 0.020]],
    text: [['--fg', 0.180, 0.020]],
    steps: [['--fg-2', 0.380, 0.024], ['--fg-3', 0.500, 0.026]],
  };

  /* the accent's lightness band per scheme: `[low, high]`. A base inside it is the accent itself. */
  const ACCENT_L = {dark: [0.62, 0.92], light: [0.38, 0.62]};

  /* the one constant of the palette the palette does not own: the live dot's green means LIVE, whatever
   * the colours are — and one value per scheme, because pale green on a light ground is 1.9:1 */
  const SAGE = {dark: '#a9c7b4', light: '#5d7f74'};

  /* the pairs the wizard is held to, in the order the sheet is checked */
  const PAIRS = [
    ['--fg', '--ground', 4.5, 'body'],
    ['--fg-2', '--ground', 4.5, 'body'],
    ['--fg-3', '--ground', 4.5, 'body'],
    ['--fg', '--panel', 4.5, 'body'],
    ['--brass', '--ground', 4.5, 'body'],
    ['--brass', '--panel', 4.5, 'body'],
    ['--brass-hi', '--ground', 4.5, 'body'],
    ['--line-2', '--ground', 3.0, 'ui'],
    ['--line-2', '--panel', 3.0, 'ui'],
    ['--sage', '--ground', 3.0, 'ui'],
  ];

  /* the handoff's declaration order, and the whole of what the wizard writes */
  const ORDER = [
    '--ground', '--stage', '--band', '--panel', '--panel-2', '--line', '--line-2',
    '--fg', '--fg-2', '--fg-3',
    '--brass', '--brass-hi', '--brass-dim', '--brass-wash', '--sage',
    '--hero-glow', '--hero-sage', '--wash', '--hair',
    '--pulse-ring', '--pulse-none', '--pulse-edge', '--black',
  ];

  /* a base whose accent has to move by more than this many OKLCH lightness points is a DIFFERENT
   * colour, not a nudge: it asks the operator before the handoff opens */
  const NUDGE = 0.05;

  const lightness = (rgb) => oklch(rgb)[0];

  /* ---------------------------------------------------------------- the follower rule */
  /* WHAT MOVES WITH WHAT — read off the derivation's own ladder, not invented: a follower sits at its
   * leader's OKLCH lightness plus the offset, at its own chroma and the leader's hue. The light page
   * runs the same dependencies the other way. */
  const FOLLOWERS = {
    '--ground': [['--stage', -0.040]],
    '--panel': [['--panel-2', 0.030], ['--band', -0.015]],
    '--line': [['--line-2', 0.100]],
    '--fg': [['--fg-2', -0.115], ['--fg-3', -0.270]],
    '--brass': [['--brass-hi', 0.090], ['--brass-dim', -0.170]],
  };
  const FOLLOWERS_LIGHT = {
    '--ground': [['--stage', -0.025]],
    '--panel': [['--panel-2', -0.020], ['--band', -0.080]],
    '--line': [['--line-2', -0.100]],
    '--fg': [['--fg-2', 0.200], ['--fg-3', 0.320]],
    '--brass': [['--brass-hi', -0.100], ['--brass-dim', 0.160]],
  };
  /* the washes are the leader at alpha: the same rgb, the alpha the sheet carries */
  const WASHES = {'--brass': [['--brass-wash', 0.10], ['--hero-glow', 0.17]]};

  const followerTable = (scheme) => (scheme === 'light' ? FOLLOWERS_LIGHT : FOLLOWERS);

  /* the follower's own chroma, read off the ladder; the accent trio is not in the ladder, so it keeps
   * the leader's chroma and the gamut clip decides how much of it survives */
  const ladderChroma = (token, scheme) => {
    let found = null;
    Object.values(scheme === 'light' ? LIGHT : DARK).forEach((rows) => rows.forEach(([name, , chroma]) => {
      if (name === token) found = chroma;
    }));
    return found;
  };

  /* every declaration a leader's edit moves, as {name: rgb} — the table is the whole of the rule */
  const followerValues = (token, rgb, scheme) => {
    const [L, C, h] = oklch(rgb);
    const out = {};
    (followerTable(scheme)[token] || []).forEach(([name, offset]) => {
      const chroma = ladderChroma(name, scheme);
      out[name] = fromOklch(Math.min(Math.max(L + offset, 0.02), 0.995),
                            chroma == null ? C : chroma, h);
    });
    (WASHES[token] || []).forEach(([name, alpha]) => { out[name] = [...rgb, alpha]; });
    return out;
  };

  /* the leader a declaration follows, or null for a free value */
  const followerOf = (token, scheme) => {
    const table = followerTable(scheme);
    let found = null;
    Object.keys(table).forEach((leader) => {
      table[leader].forEach(([name, offset]) => {
        if (name === token) found = {leader, offset};
      });
    });
    return found;
  };

  /* ONE hand-set declaration: the token, the followers it moves, and every pair re-measured. Nothing
   * is clamped: the operator's value is the value the sheet would carry. */
  const editValues = (result, token, value) => {
    const rgb = hexRgb(value);
    if (!rgb) {
      return null;
    }
    const tokens = {};
    ORDER.forEach((name) => {
      if (result.tokens[name]) tokens[name] = result.tokens[name].slice();
    });
    const moved = followerValues(token, rgb, result.scheme);
    const followers = [];
    Object.keys(moved).forEach((name) => {
      const was = result.tokens[name];
      followers.push({name, value: moved[name],
                      offset: (followerOf(name, result.scheme) || {}).offset,
                      from: was ? lightness(was) : null, to: lightness(moved[name])});
    });
    tokens[token] = rgb;
    Object.keys(moved).forEach((name) => { tokens[name] = moved[name]; });
    const next = finish(tokens, {moves: [], followers,
                                 edit: {token, value: rgb, from: lightness(result.tokens[token]),
                                        to: lightness(rgb)}}, result.scheme);
    next.gated = false;
    next.adjusted = false;
    next.baseLch = null;
    next.report = false;
    next.edit.pairs = next.checks.filter((check) => check.token === token || check.against === token
      || (followerOf(check.token, result.scheme) || {}).leader === token
      || (followerOf(check.against, result.scheme) || {}).leader === token);
    return next;
  };

  /* the nearest value of the edited token that clears every pair the edit takes part in; null when
   * no lightness reaches. The followers are recomputed at each candidate, so a follower's pair is
   * part of the answer. */
  const nearestEdit = (result, token, against) => {
    const rgb = result.tokens[token];
    if (!rgb) {
      return null;
    }
    const [L, C, h] = oklch(rgb);
    const involved = (check) => check.token === token || check.against === token
      || (followerOf(check.token, result.scheme) || {}).leader === token
      || (followerOf(check.against, result.scheme) || {}).leader === token;
    for (let step = 1; step <= 40; step += 1) {
      for (const sign of [1, -1]) {
        const candidate = fromOklch(Math.min(Math.max(L + sign * 0.01 * step, 0.02), 0.995), C, h);
        const tokens = {};
        ORDER.forEach((name) => {
          if (result.tokens[name]) tokens[name] = result.tokens[name].slice();
        });
        tokens[token] = candidate;
        const moved = followerValues(token, candidate, result.scheme);
        Object.keys(moved).forEach((name) => { tokens[name] = moved[name]; });
        const checks = PAIRS.map(([a, b, min, role]) => {
          const ratio = contrast(tokens[a], tokens[b]);
          return {token: a, against: b, min, role, ratio, pass: ratio >= min};
        });
        const relevant = checks.filter(involved);
        if (relevant.every((check) => check.pass)) {
          const on = relevant.find((check) => check.token === token && check.against === against)
            || relevant.find((check) => check.token === token) || relevant[0];
          const ground = relevant.find((check) => check.token === token && check.against === '--ground');
          return {value: rgbHex(candidate), from: L, to: lightness(candidate), checks: relevant,
                  on: on ? on.ratio : null, against: on ? on.against : against,
                  ground: ground ? ground.ratio : (on && on.against === '--ground' ? on.ratio : null)};
        }
      }
    }
    return null;
  };

  /* raise (dark) or lower (light) every failing token's lightness until its pairs clear */
  const enforce = (tokens, scheme) => {
    const moves = [];
    const up = scheme === 'dark' ? 1 : -1;
    PAIRS.forEach(([token, against, minimum, role]) => {
      const before = tokens[token];
      if (!before || !tokens[against]) {
        return;
      }
      const ratio = contrast(before, tokens[against]);
      if (ratio >= minimum) {
        return;
      }
      const [L, C, h] = oklch(before);
      for (let step = 1; step <= 40; step += 1) {
        const candidate = fromOklch(Math.min(Math.max(L + up * 0.01 * step, 0.02), 0.995), C, h);
        if (contrast(candidate, tokens[against]) >= minimum) {
          tokens[token] = candidate;
          moves.push({token, against, min: minimum, role, was: ratio,
                      from: L, to: lightness(candidate),
                      reached: contrast(candidate, tokens[against])});
          return;
        }
      }
      moves.push({token, against, min: minimum, role, was: ratio, from: L, to: L,
                  reached: ratio, failed: true});
    });
    return moves;
  };

  /* the washes, the six constants and the measured pairs: one shape for every starting point */
  const finish = (tokens, extra, scheme) => {
    tokens['--brass-wash'] = [...tokens['--brass'], 0.10];
    tokens['--hero-glow'] = [...tokens['--brass'], 0.17];
    tokens['--hero-sage'] = [...tokens['--sage'], 0.05];
    tokens['--pulse-ring'] = [...tokens['--sage'], 0.55];
    tokens['--pulse-none'] = [...tokens['--sage'], 0];
    tokens['--pulse-edge'] = [...tokens['--sage'], 0.35];
    tokens['--wash'] = [...tokens['--fg'], 0.06];
    tokens['--hair'] = [...tokens['--fg'], 0.06];
    tokens['--black'] = [0, 0, 0];
    const checks = PAIRS.map(([token, against, minimum, role]) => {
      const ratio = contrast(tokens[token], tokens[against]);
      return {token, against, min: minimum, role, ratio, pass: ratio >= minimum};
    });
    const values = {};
    ORDER.forEach((name) => { values[name] = rgbHex(tokens[name]); });
    return {...extra, scheme, tokens, values, checks, declarations: ORDER.length,
            pairs: PAIRS.length, failed: checks.filter((check) => !check.pass)};
  };

  /* a base colour -> the wizard's six steps, the accent it had to use, and the moves it made */
  const derive = (baseHex, scheme) => {
    const base = hexRgb(baseHex);
    if (!base) {
      return null;
    }
    const [L, C, h] = oklch(base);
    const [lo, hi] = ACCENT_L[scheme];
    /* the accent: the base at the ladder's lightness band, chroma floored so a grey still carries a hue */
    const aL = Math.min(Math.max(L, lo), hi);
    const aC = Math.max(C, 0.075);
    const accent = fromOklch(aL, aC, h);
    const tokens = {};
    Object.values(scheme === 'dark' ? DARK : LIGHT).forEach((rows) => rows.forEach(([name, tL, tC]) => {
      tokens[name] = fromOklch(tL, tC, h);
    }));
    tokens['--brass'] = accent;
    tokens['--brass-hi'] = fromOklch(
      scheme === 'dark' ? Math.min(aL + 0.09, 0.97) : Math.max(aL - 0.10, 0.06), aC + 0.012, h);
    tokens['--brass-dim'] = fromOklch(
      scheme === 'dark' ? Math.max(aL - 0.17, 0.10) : Math.min(aL + 0.16, 0.90),
      Math.max(aC - 0.015, 0.04), h);
    tokens['--sage'] = hexRgb(SAGE[scheme]);
    const moves = enforce(tokens, scheme);
    const result = finish(tokens, {
      base, baseLch: [L, C, h], accent, moves,
      adjusted: rgbHex(accent) !== rgbHex(base),
    }, scheme);
    result.gated = Math.abs(aL - L) > NUDGE;
    return result;
  };

  /* ONE sheet from a set of values: the sheet's own grammar, one declaration per line */
  const sheetText = (values) => ORDER.map((name) => `${name}: ${values[name]};`).join('\n');

  /* a sheet's declarations as `{name: value}` — a line the grammar does not describe is skipped */
  const parseSheet = (text) => {
    const values = {};
    String(text || '').split('\n').forEach((raw) => {
      const found = raw.trim().match(/^(--[a-z0-9-]{1,64})\s*:\s*(.*?)\s*;$/);
      if (found) {
        values[found[1]] = found[2];
      }
    });
    return values;
  };

  /* a palette read off a document's root: the wizard's tokens, with the value the browser resolved */
  const readRoot = (doc) => {
    if (!doc || !doc.documentElement || !doc.defaultView) {
      return null;
    }
    const computed = doc.defaultView.getComputedStyle(doc.documentElement);
    const values = {};
    ORDER.forEach((name) => {
      const raw = String(computed.getPropertyValue(name) || '').trim();
      const rgb = colourRgb(raw);
      if (rgb) {
        values[name] = {raw, rgb};
      }
    });
    return Object.keys(values).length ? values : null;
  };

  /* a palette read off a sheet's text: the values that are colours, the rest skipped */
  const readSheet = (text) => {
    const declared = parseSheet(text);
    const values = {};
    ORDER.forEach((name) => {
      const rgb = colourRgb(declared[name]);
      if (rgb) {
        values[name] = {raw: declared[name], rgb};
      }
    });
    return Object.keys(values).length ? values : null;
  };

  /* a palette that came from somewhere other than the ladder: measured, moved, and handed back in the
   * derivation's shape so every screen downstream reads one object. `report` MEASURES AND DOES NOT
   * MOVE: a palette that was READ (a stored sheet, the page's own colours) is reported as it stands,
   * and the solver runs only on a derivation or on an explicit press. */
  const fromValues = (values, scheme, extra) => {
    const tokens = {};
    ORDER.forEach((name) => {
      if (values[name]) {
        tokens[name] = values[name].rgb;
      }
    });
    if (ORDER.some((name) => !tokens[name])) {
      return null;
    }
    const moves = extra && extra.report ? [] : enforce(tokens, scheme);
    return finish(tokens, {moves, adjusted: false, gated: false, ...extra}, scheme);
  };

  /* ONE paint of a preview's root: every value that is set, as an inline custom property, and the
   * scheme on `data-theme`. The wizard's own preview and the palette block's controls both write
   * through this, so a preset press and a derivation land on the frame the same way. A declaration
   * a caller does not carry is left as the frame has it — the last valid state, never emptied. */
  const paintRoot = (doc, values, scheme) => {
    if (!doc || !doc.documentElement) {
      return null;
    }
    const root = doc.documentElement;
    Object.keys(values || {}).forEach((name) => {
      if (values[name]) {
        root.style.setProperty(name, values[name]);
      }
    });
    if (scheme) {
      root.setAttribute('data-theme', scheme);
    }
    return root;
  };

  window.SC_PALETTE = {derive, enforce, fromValues, sheetText, parseSheet, readRoot, readSheet,
                       colourRgb, rgbHex, hexRgb, oklch, contrast, STEPS, PAIRS, ORDER, NUDGE, SAGE,
                       FOLLOWERS, FOLLOWERS_LIGHT, WASHES, followerOf, followerValues, editValues,
                       nearestEdit, paintRoot};

  /* ================================================================ the screen */
  const fill = (pattern, values) => {
    let text = String(pattern == null ? '' : pattern);
    Object.keys(values || {}).forEach((key) => {
      text = text.split('%(' + key + ')s').join(String(values[key]));
    });
    return text;
  };

  const fixed = (value, places) => Number(value || 0).toFixed(places);

  /* how many declarations a sheet's text carries: what the head counts, and nothing else */
  const declarations = (text) => String(text || '').split('\n').filter((line) => line.trim()).length;

  document.querySelectorAll('[data-sc-wizard]').forEach((block) => {
    let data;
    try {
      data = JSON.parse(block.getAttribute('data-sc-wizard') || '{}');
    } catch (error) {
      return;
    }
    const card = block.closest('form') || document;
    const words = data.words || {};
    /* the block's own panels: the gallery at rest, and this wizard — exactly one is drawn */
    const rest = card.querySelector('[data-sc-pal-panel="rest"]');
    const door = card.querySelector('[data-pal-start]');
    const closeButton = block.querySelector('[data-sc-wiz-close]');
    const storedStrip = block.querySelector('[data-sc-wiz-stored]');
    const stepRows = [...block.querySelectorAll('[data-sc-wiz-step]')];
    const starts = [...block.querySelectorAll('[data-sc-wiz-start]')];
    const schemePills = [...block.querySelectorAll('[data-sc-wiz-scheme]')];
    const colour = block.querySelector('[data-sc-wiz-color]');
    const hexField = block.querySelector('[data-sc-wiz-hex]');
    const where = block.querySelector('[data-sc-wiz-where]');
    const bodyOne = block.querySelector('[data-sc-wiz-body="1"]');
    const bodyTwo = block.querySelector('[data-sc-wiz-body="2"]');
    const deriveButton = block.querySelector('[data-sc-wiz-derive]');
    const useButton = block.querySelector('[data-sc-wiz-use]');
    const warning = block.querySelector('[data-sc-wiz-warn]');
    const accept = block.querySelector('[data-sc-wiz-accept]');
    const keep = block.querySelector('[data-sc-wiz-keep]');
    const pick = block.querySelector('[data-sc-wiz-pick]');
    const moves = block.querySelector('[data-sc-wiz-moves]');
    const constants = block.querySelector('[data-sc-wiz-consts]');
    const nowNote = block.querySelector('[data-sc-wiz-now]');
    const foot = block.querySelector('[data-sc-wiz-foot]');
    const preview = card.querySelector('[data-sc-preview]');
    const expert = card.querySelector('[data-sc-expert]');
    const handed = card.querySelector('[data-sc-handed]');
    if (!stepRows.length || !starts.length) {
      return;
    }

    const state = {start: 'preset', base: (colour && colour.value) || '#4a7fd1', scheme: 'dark',
                   result: null, gated: false, injected: null, editWarn: null, kept: false};

    /* ---------------- the preview document: the site's own, with the candidate on its root */
    const previewDoc = () => {
      if (!preview) {
        return null;
      }
      const doc = preview.contentDocument;
      return doc && doc.documentElement ? doc : null;
    };
    const paintPreview = (values) => {
      const doc = previewDoc();
      if (!doc) {
        return;
      }
      if (state.injected === null) {
        state.injected = {theme: doc.documentElement.getAttribute('data-theme')};
      }
      paintRoot(doc, values, state.scheme);
    };
    const clearPreview = () => {
      const doc = previewDoc();
      if (!doc || state.injected === null) {
        return;
      }
      ORDER.forEach((name) => doc.documentElement.style.removeProperty(name));
      if (state.injected.theme === null) {
        doc.documentElement.removeAttribute('data-theme');
      } else {
        doc.documentElement.setAttribute('data-theme', state.injected.theme);
      }
      state.injected = null;
    };

    /* ---------------- what the six rows print: the hex of every value, and the tested pair */
    const paintSteps = (result) => {
      const measured = {};
      result.checks.forEach((check) => { measured[check.token + '|' + check.against] = check; });
      stepRows.forEach((row) => {
        const spec = STEPS.find((entry) => entry.id === row.getAttribute('data-sc-wiz-step'));
        if (!spec) {
          return;
        }
        const hexes = spec.tokens.map((name) => result.values[name] || '');
        const swatch = row.querySelector('.sw');
        if (swatch) {
          swatch.style.background = hexes[0];
          if (hexRgb(hexes[0])) {
            swatch.value = hexes[0];
          }
        }
        const hex = row.querySelector('[data-sc-wiz-hex]');
        if (hex) {
          hex.textContent = hexes.join(' ');
        }
        const check = measured[spec.pair[0] + '|' + spec.pair[1]];
        const chip = row.querySelector('[data-sc-wiz-ratio]');
        if (chip && check) {
          chip.textContent = fixed(check.ratio, 2) + ':1';
          chip.dataset.state = check.pass ? 'ok' : 'bad';
          chip.style.setProperty('--k', check.pass ? 'var(--ok)' : 'var(--danger)');
          chip.style.setProperty('--kw', check.pass ? 'var(--ok-wash)' : 'var(--danger-wash)');
        }
        const pair = row.querySelector('[data-sc-wiz-pair]');
        if (pair) {
          pair.textContent = spec.pair[0] + ' / ' + spec.pair[1];
        }
      });
    };

    /* the offset a follower's line names, signed and trimmed: +0.09, −0.04, −0.115 */
    const offsetText = (offset) => {
      const value = Number(offset) || 0;
      const sign = value < 0 ? '−' : '+';
      const text = Math.abs(value).toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
      return sign + text;
    };

    /* the declarations behind one row: the value, the hex, the name and what the name is. A follower
     * is stated as following its leader and the offset; its inputs are not a second free value. */
    const paintTokens = (result) => {
      const moved = {};
      (result.followers || []).forEach((entry) => { moved[entry.name] = entry; });
      stepRows.forEach((row) => {
        const spec = STEPS.find((entry) => entry.id === row.getAttribute('data-sc-wiz-step'));
        if (!spec) {
          return;
        }
        const toks = row.querySelector('[data-sc-wiz-toks]');
        if (!toks) {
          return;
        }
        spec.tokens.forEach((name) => {
          const cell = toks.querySelector('[data-sc-wiz-tok="' + name + '"]');
          if (!cell) {
            return;
          }
          const value = result.values[name];
          const follower = followerOf(name, result.scheme);
          const field = cell.querySelector('[data-sc-wiz-tokpick]');
          const hex = cell.querySelector('[data-sc-wiz-tokhex]');
          const said = cell.querySelector('[data-sc-wiz-toksaid]');
          if (field) {
            field.value = hexRgb(value) ? value : '';
            field.disabled = Boolean(follower);
          }
          if (hex) {
            hex.value = value;
            hex.disabled = Boolean(follower);
            hex.readOnly = Boolean(follower);
          }
          if (said) {
            if (follower) {
              const move = moved[name];
              said.textContent = fill(words.follows, {token: follower.leader,
                                                      offset: offsetText(follower.offset)})
                + (move ? ' · ' + fill(words.moved_l, {from: fixed(move.from, 2),
                                                       to: fixed(move.to, 2)}) : '');
            } else {
              said.textContent = name === '--brass' ? (words.accent || '') : (words.own || '');
            }
          }
        });
        const pair = toks.querySelector('[data-sc-wiz-tokpair]');
        if (pair) {
          const check = result.checks.find((c) => c.token === spec.pair[0]
                                              && c.against === spec.pair[1]);
          if (check) {
            const under = check.pass ? '' : (words.under_aa || '') + ' · ';
            pair.innerHTML = '<b>' + spec.pair[0] + ' on ' + spec.pair[1] + '</b> · '
              + check.role + ' ' + fixed(check.min, 1) + ':1 · ' + fixed(check.ratio, 2) + ':1 · '
              + under + (words.step_editing || '');
          }
        }
        /* THE ROW'S CLOSED SWATCH IS NOT A SECOND OWNER. When the step's first declaration follows a
         * leader, the closed swatch states the follower's own line and stops taking edits; the row's
         * opener still opens the step, where the follower's inputs are disabled the same way. */
        const closed = row.querySelector('[data-sc-wiz-pick]');
        if (closed) {
          const owner = followerOf(spec.tokens[0], result.scheme);
          closed.disabled = Boolean(owner);
          const label = owner
            ? fill(words.follows, {token: owner.leader, offset: offsetText(owner.offset)})
            : spec.tokens[0];
          closed.setAttribute('aria-label', label);
          closed.setAttribute('title', label);
        }
      });
    };

    const warnSay = block.querySelector('[data-sc-wiz-warn-say]');

    /* ---------------- what the rule had to move, in the note's own grammar */
    const paintMoves = (result) => {
      if (!moves) {
        return;
      }
      moves.replaceChildren();
      const lines = [];
      if (result.baseLch) {
        const [L, C] = result.baseLch;
        const accent = rgbHex(result.accent);
        if (result.gated) {
          const need = ACCENT_L[result.scheme][result.scheme === 'dark' ? 0 : 1];
          if (warnSay) {
            warnSay.textContent = fill(words.adjust, {base: rgbHex(result.base), accent,
                                                      from: fixed(L, 2), to: fixed(need, 2)});
          }
        } else if (result.adjusted) {
          lines.push(fill(words.used, {base: rgbHex(result.base), accent, from: fixed(L, 2),
                                       to: fixed(oklch(result.accent)[0], 2)}));
          if (C < 0.075) {
            lines.push(fill(words.chroma, {from: fixed(C, 3), to: fixed(0.075, 3)}));
          }
        }
      }
      if (!result.gated && warnSay) {
        warnSay.textContent = '';
      }
      result.moves.forEach((move) => {
        lines.push(fill(move.failed ? words.stuck : words.moved, {
          token: move.token, against: move.against, from: fixed(move.from, 2),
          to: fixed(move.to, 2), ratio: fixed(move.reached, 2), was: fixed(move.was, 2),
          role: move.role, needs: fixed(move.min, 1)}));
      });
      lines.forEach((text) => {
        const line = document.createElement('p');
        line.className = 'sc-adj-note';
        line.textContent = text;
        moves.appendChild(line);
      });
      moves.hidden = !lines.length;
    };

    const paintStored = (result) => {
      if (storedStrip) {
        storedStrip.textContent = words.reported || storedStrip.textContent;
        storedStrip.hidden = !result.report;
      }
    };

    const paintGate = (result) => {
      state.gated = Boolean(result.gated);
      if (state.gated && accept) {
        accept.textContent = fill(words.use_this, {hex: rgbHex(result.accent)});
      }
    };

    /* the strip's one warning slot serves the base-colour gate and a hand-set value under AA. The
     * value's warning wins: it names the nearest that clears and the operator's own value beside it. */
    const paintWarn = () => {
      if (!warning) {
        return;
      }
      const edit = state.editWarn;
      if (state.kept && state.result && state.result.edit) {
        const failed = state.result.edit.pairs.filter((pair) => !pair.pass);
        if (foot && words.edit_foot) {
          foot.textContent = fill(words.edit_foot, {count: failed.length});
        }
        if (useButton) {
          useButton.disabled = true;
        }
        warning.hidden = true;
        return;
      }
      if (edit) {
        warning.hidden = false;
        if (warnSay) {
          warnSay.textContent = edit.nearest
            ? fill(words.edit_under, {before: rgbHex(edit.value), hex: edit.nearest.value,
                                      ratio: fixed(edit.nearest.on, 2), against: edit.nearest.against,
                                      ground: fixed(edit.nearest.ground, 2),
                                      from: fixed(edit.nearest.from, 2),
                                      to: fixed(edit.nearest.to, 2)})
            : fill(words.edit_stuck, {token: edit.token, hex: rgbHex(edit.value),
                                      against: edit.pair.against,
                                      needs: fixed(edit.pair.min, 1)});
        }
        if (accept) {
          accept.textContent = edit.nearest ? fill(words.use_this, {hex: edit.nearest.value}) : '';
          accept.hidden = !edit.nearest;
        }
        if (keep) {
          keep.textContent = fill(words.edit_keep, {hex: rgbHex(edit.value)});
          keep.hidden = false;
        }
        if (pick) {
          pick.hidden = true;
        }
        if (useButton) {
          useButton.disabled = true;
        }
        if (foot && words.edit_foot) {
          foot.textContent = fill(words.edit_foot, {count: edit.count});
        }
        return;
      }
      if (keep) {
        keep.hidden = true;
      }
      if (pick) {
        pick.hidden = false;
      }
      if (foot) {
        foot.textContent = data.foot || foot.textContent;
      }
      if (state.gated) {
        warning.hidden = false;
        if (accept) {
          accept.hidden = false;
        }
        if (useButton) {
          useButton.disabled = true;
        }
        return;
      }
      warning.hidden = true;
      if (useButton) {
        useButton.disabled = false;
      }
    };

    /* a hand-set value: the pairs it takes part in, the nearest that clears them, and nothing clamped */
    const paintEdit = (result) => {
      state.editWarn = null;
      const edit = result.edit;
      if (!edit) {
        paintWarn();
        return;
      }
      const failed = edit.pairs.filter((pair) => !pair.pass);
      if (!failed.length) {
        paintWarn();
        return;
      }
      /* the strip answers the WORST pair — the one with the lowest measured ratio — so its sentence
       * states the pair the operator has to clear first, not the first in the table's order */
      const pair = failed.reduce((worst, check) => (check.ratio < worst.ratio ? check : worst));
      const nearest = nearestEdit(result, edit.token, pair.against);
      state.editWarn = {token: edit.token, value: edit.value, pair, nearest, count: failed.length};
      paintWarn();
    };

    /* one hand-set declaration: the value, its followers and every pair re-measured — nothing clamped */
    const applyEdit = (token, value) => {
      if (!state.result) {
        return null;
      }
      const next = editValues(state.result, token, value);
      if (!next) {
        return null;
      }
      state.kept = false;
      repaint(next);
      return next;
    };

    /* the row's declaration list opens and closes on its chevron; a closed row stays the read-only row */
    const openRow = (row, open) => {
      row.classList.toggle('is-open', open);
      const toks = row.querySelector('[data-sc-wiz-toks]');
      if (toks) {
        toks.hidden = !open;
      }
      const more = row.querySelector('[data-sc-wiz-more]');
      if (more) {
        more.setAttribute('aria-expanded', open ? 'true' : 'false');
        const cv = more.querySelector('.cv');
        if (cv) {
          cv.textContent = open ? '▴' : '▾';
        }
      }
    };
    stepRows.forEach((row) => {
      const more = row.querySelector('[data-sc-wiz-more]');
      if (more) {
        more.addEventListener('click', (event) => {
          event.preventDefault();
          openRow(row, !row.classList.contains('is-open'));
        });
      }
      const spec = STEPS.find((entry) => entry.id === row.getAttribute('data-sc-wiz-step'));
      const swatch = row.querySelector('[data-sc-wiz-pick]');
      if (swatch && spec) {
        swatch.addEventListener('input', () => {
          const next = applyEdit(spec.tokens[0], swatch.value);
          if (next) {
            swatch.style.background = swatch.value;
          }
        });
      }
      row.querySelectorAll('[data-sc-wiz-tokpick]').forEach((field) => {
        field.addEventListener('input', () => {
          const token = field.getAttribute('data-sc-wiz-tokpick');
          const next = applyEdit(token, field.value);
          if (next) {
            const hex = field.parentElement.querySelector('[data-sc-wiz-tokhex]');
            if (hex) {
              hex.value = field.value;
            }
          }
        });
      });
      row.querySelectorAll('[data-sc-wiz-tokhex]').forEach((field) => {
        field.addEventListener('input', () => {
          const token = field.getAttribute('data-sc-wiz-tokhex');
          if (!hexRgb(field.value)) {
            return;
          }
          const value = '#' + field.value.trim().replace(/^#/, '').toLowerCase();
          const next = applyEdit(token, value);
          if (next) {
            const picker = field.parentElement.querySelector('[data-sc-wiz-tokpick]');
            if (picker) {
              picker.value = value;
            }
          }
        });
      });
    });

    const show = (which, scroll = true) => {
      if (bodyOne) {
        bodyOne.hidden = which !== 1;
      }
      if (bodyTwo) {
        bodyTwo.hidden = which !== 2;
      }
      if (where) {
        where.textContent = which === 1 ? (words.step1 || '') : (words.step2 || '');
      }
      block.dataset.step = String(which);
      if (scroll) {
        block.scrollIntoView({block: 'nearest'});
      }
      if (which === 1) {
        clearPreview();
      }
    };

    /* the block draws ONE panel: at rest the gallery, and behind the door this wizard */
    const openWizard = () => {
      if (rest) {
        rest.hidden = true;
      }
      block.hidden = false;
      show(1);
    };
    const closeWizard = () => {
      block.hidden = true;
      if (rest) {
        rest.hidden = false;
      }
      clearPreview();
    };
    if (door) {
      door.addEventListener('click', (event) => {
        event.preventDefault();
        openWizard();
      });
    }
    if (closeButton) {
      closeButton.addEventListener('click', (event) => {
        event.preventDefault();
        closeWizard();
      });
    }

    /* ---------------- the three starting points */
    const press = (chosen, value) => {
      starts.forEach((tile) => {
        const on = tile === chosen;
        tile.classList.toggle('on', on);
        const radio = tile.querySelector('input');
        if (radio) {
          radio.checked = on;
        }
      });
      state.start = value;
    };
    starts.forEach((tile) => {
      tile.addEventListener('click', (event) => {
        const radio = tile.querySelector('input');
        if (radio && radio.disabled) {
          event.preventDefault();
          return;
        }
        event.preventDefault();
        press(tile, radio ? radio.value : (tile.getAttribute('data-sc-wiz-start') || ''));
      });
    });
    const pressScheme = (scheme) => {
      state.scheme = scheme;
      schemePills.forEach((pill) => pill.classList.toggle('on', pill.value === scheme));
    };
    schemePills.forEach((pill) => {
      pill.addEventListener('click', (event) => {
        event.preventDefault();
        pressScheme(pill.value);
      });
    });
    const oneTile = () => starts.find((tile) => (tile.querySelector('input') || {}).value === 'one');
    if (colour) {
      colour.addEventListener('input', () => {
        state.base = colour.value;
        if (hexField) {
          hexField.value = colour.value;
        }
        if (oneTile()) {
          press(oneTile(), 'one');
        }
      });
    }
    if (hexField) {
      hexField.addEventListener('input', () => {
        if (!hexRgb(hexField.value)) {
          return;
        }
        state.base = '#' + hexField.value.trim().replace(/^#/, '').toLowerCase();
        if (colour) {
          colour.value = state.base;
        }
        if (oneTile()) {
          press(oneTile(), 'one');
        }
      });
    }

    /* ---------------- derive, and show what it found */
    /* repaint the six rows, the preview and the strip from ONE result — never reload the frame */
    const repaint = (result) => {
      state.result = result;
      paintSteps(result);
      paintTokens(result);
      paintMoves(result);
      paintStored(result);
      paintGate(result);
      paintEdit(result);
      paintPreview(result.values);
    };

    const showResult = (result) => {
      if (!result) {
        return;
      }
      repaint(result);
      show(2);
    };

    /* the local name is not `derive`: that is the module's own function, and shadowing it here
     * would make the button call itself */
    const deriveNow = () => {
      const preset = () => showResult(fromValues(readSheet(data.prefill), state.scheme,
                                                 {preset: true}));
      if (state.start === 'now') {
        const values = readRoot(previewDoc());
        if (!values) {
          if (nowNote) {
            nowNote.hidden = false;
          }
          return;
        }
        /* the page's own colours: measured as they stand, never moved */
        showResult(fromValues(values, state.scheme, {now: true, report: true}));
        return;
      }
      if (state.start === 'stored' && expert) {
        const text = expert.querySelector('[data-sc-x-text]');
        const built = fromValues(readSheet(text ? text.value : ''), state.scheme,
                                 {stored: true, report: true});
        /* a sheet that does not carry every token cannot be read back: answer with the shipped
         * transcription rather than leave the press unanswered */
        if (built) {
          showResult(built);
        } else {
          preset();
        }
        return;
      }
      if (state.start === 'preset') {
        preset();
        return;
      }
      showResult(window.SC_PALETTE.derive(state.base, state.scheme));
    };

    if (deriveButton) {
      deriveButton.addEventListener('click', (event) => {
        event.preventDefault();
        deriveNow();
      });
    }
    if (accept) {
      accept.addEventListener('click', (event) => {
        event.preventDefault();
        if (!state.result) {
          return;
        }
        /* a hand-set value under AA: the offer IS the value, so pressing it applies it and the pair
         * is re-measured; the base gate's offer applies the moved accent, as it always has */
        if (state.editWarn) {
          const edit = state.editWarn;
          if (!edit.nearest) {
            return;
          }
          const next = editValues(state.result, edit.token, edit.nearest.value);
          if (next) {
            state.kept = false;
            repaint(next);
          }
          return;
        }
        state.base = rgbHex(state.result.accent);
        state.result.gated = false;
        state.result.baseLch = null;
        paintMoves(state.result);
        paintGate(state.result);
        paintWarn();
      });
    }
    if (keep) {
      keep.addEventListener('click', (event) => {
        event.preventDefault();
        if (!state.editWarn) {
          return;
        }
        /* the operator is the owner: the value stays, the row keeps its red chip, and the handoff
         * stays held — keeping is an answer, not a fix */
        state.kept = true;
        state.editWarn = null;
        paintWarn();
      });
    }
    if (pick) {
      pick.addEventListener('click', (event) => {
        event.preventDefault();
        show(1);
        if (colour) {
          colour.focus();
        }
      });
    }
    block.querySelectorAll('[data-sc-wiz-back]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        show(1);
      });
    });

    /* ---------------- the handoff: the sheet, and the way back */
    /* WHICH OF THE THREE the site names is the page's own table (`data-sc-pal` on the block), so the
     * head is repainted from it rather than from a second copy of the words */
    const headPayload = () => {
      const host = card.querySelector('[data-sc-pal]');
      try {
        return JSON.parse((host && host.getAttribute('data-sc-pal')) || '{}');
      } catch (error) {
        return {};
      }
    };
    const paintHead = (ident, count) => {
      const states = headPayload().states || {};
      const state = states[ident];
      if (!state) {
        return;
      }
      const wordsNode = card.querySelector('[data-sc-pal-words]');
      const keyNode = card.querySelector('[data-sc-pal-key]');
      const valueNode = card.querySelector('[data-sc-pal-value]');
      if (wordsNode) {
        wordsNode.textContent = state.words;
      }
      if (keyNode) {
        keyNode.textContent = state.key;
      }
      if (valueNode) {
        valueNode.textContent = count == null ? state.value : fill(state.value, {declarations: count});
      }
    };
    /* THE HANDOFF'S DESTINATION IS THE SHEET, so the block shows it: the view control is ticked and
     * the sheet revealed. The CARRIER was written by the handoff above and by nothing else — the
     * box writes no sheet and clearing nothing is its rule. */
    const revealSheet = (text) => {
      paintHead('expert', declarations(text));
      const toggle = card.querySelector('[data-sc-expert-toggle]');
      if (toggle && !toggle.checked) {
        toggle.checked = true;
        toggle.dispatchEvent(new Event('change', {bubbles: true}));
      } else if (expert) {
        expert.dispatchEvent(new CustomEvent('sc-expert-shown'));
      }
    };
    const handoff = () => {
      const result = state.result;
      if (!result || state.gated || state.kept) {
        return;
      }
      const text = sheetText(result.values);
      if (expert) {
        expert.dispatchEvent(new CustomEvent('sc-sheet', {detail: text}));
        const verdict = expert.querySelector('[data-sc-x-verdict]');
        if (verdict) {
          verdict.textContent = fill(words.accepted, {declarations: result.declarations,
                                                      pairs: result.pairs});
          verdict.className = 'sc-verdict ok';
          verdict.dataset.state = 'ok';
        }
      }
      /* ONE PALETTE, ONE CHOICE: the preset is RELEASED. The empty radio is checked so the save posts
       * `theme.preset: ""`, and NO tile is marked — the site names a sheet now, and a press after
       * this paints the gallery as it always does. */
      const shipped = card.querySelector('.sc-gallery [data-sc-preset-tile] input[type="radio"]');
      if (shipped) {
        shipped.checked = true;
      }
      card.querySelectorAll('.sc-gallery [data-sc-preset-tile]').forEach((tile) => {
        tile.classList.remove('on');
        const badge = tile.querySelector('[data-sc-preset-badge]');
        if (badge) {
          badge.hidden = true;
        }
      });
      block.hidden = true;
      if (rest) {
        rest.hidden = false;
      }
      if (handed) {
        handed.hidden = false;
      }
      revealSheet(text);
    };
    if (useButton) {
      useButton.addEventListener('click', (event) => {
        event.preventDefault();
        handoff();
      });
    }
    if (handed) {
      const again = handed.querySelector('[data-sc-wiz-again]');
      if (again) {
        again.addEventListener('click', (event) => {
          event.preventDefault();
          handed.hidden = true;
          block.hidden = false;
          if (rest) {
            rest.hidden = true;
          }
          show(2);
        });
      }
    }

    if (constants) {
      constants.textContent = words.consts || constants.textContent;
    }
    const doc = previewDoc();
    pressScheme(data.scheme || (doc && doc.documentElement.getAttribute('data-theme')) || 'dark');
    const first = starts.find((tile) => (tile.querySelector('input') || {}).value === (data.start || 'preset'))
      || starts[0];
    press(first, (first.querySelector('input') || {}).value || 'preset');
    show(1, false);
    /* THE WIZARD STAYS HIDDEN AT REST: the gallery's door is the only thing that shows it, so a page
     * with scripts off keeps the sheet and loses the wizard — a wizard that cannot derive is a form
     * that lies. */
  });
})();
