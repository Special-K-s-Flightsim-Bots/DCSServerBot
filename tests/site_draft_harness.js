/* Drives the shipped `static/site-config.js` under a stub DOM and reports, for each scenario, how many
 * draft posts it made and how many times it reloaded the card's preview frame.
 *
 * The script is loaded as the browser loads it — the real file, executed — and only its surroundings
 * (document, form, panel, frame, fetch, FormData) are stand-ins. The FormData double carries the
 * form's values, so a scenario that changes one changes the signature the script compares against.
 *
 * Usage:
 *
 *     node site_draft_harness.js <path to site-config.js>
 *
 * Prints one JSON object: {"<scenario>": {"posts": n, "reloads": n, "src_changed": bool}, ...}
 */
'use strict';

const fs = require('fs');

const scriptPath = process.argv[2];
if (!scriptPath) {
  console.error('usage: node site_draft_harness.js <site-config.js>');
  process.exit(2);
}
const source = fs.readFileSync(scriptPath, 'utf8');

const startSrc = 'http://preview.test/chrome-preview?v=0';

/** One run of the shipped file: fresh counters, its own form/panel/frame doubles. */
function mount() {
  const posts = [];
  const counters = { reloads: 0 };
  const handlers = {};
  const fields = new Map([['f.sites.guards.branding.title', 'Community']]);

  const frame = {
    currentSrc: startSrc,
    get src() { return this.currentSrc; },
    set src(value) { this.currentSrc = value; counters.reloads += 1; },
    contentDocument: null,
    dataset: {},
    addEventListener() {},
    setAttribute() {},
  };
  const box = { className: '', replaceChildren() {}, setAttribute() {}, removeAttribute() {} };
  const row = { getAttribute: (attr) => (attr === 'data-sc-drives' ? (row.drives || '') : null),
                drives: '' };
  const card = { addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; } };
  const form = {
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    querySelectorAll() { return []; },
    closest() { return form; },
  };
  const panel = {
    closest(which) { return which === 'form' ? form : card; },
    getAttribute(attr) {
      return {
        'data-sc-draft-url': '/actions/settings/site/draft',
        'data-sc-preview-url': 'http://preview.test/chrome-preview',
        'data-sc-draft-clear': '_sc_draft_clear',
        'data-sc-preview-title': 'preview',
        'data-sc-preview-off': 'off',
      }[attr] || '';
    },
    querySelector(sel) {
      if (sel === 'iframe[data-sc-preview]') { return frame; }
      if (sel === '.sc-pv-box') { return box; }
      return null;
    },
    querySelectorAll() { return []; },
    dispatchEvent() {},
  };
  const document = {
    addEventListener() {},
    querySelectorAll(sel) { return sel === 'aside[data-sc-site]' ? [panel] : []; },
    querySelector() { return null; },
  };

  class StubFormData {
    constructor() { this.pairs = new Map(fields); }
    delete(name) { this.pairs.delete(name); }
    set(name, value) { this.pairs.set(name, value); }
    append(name, value) { this.pairs.set(name, value); }
    has(name) { return this.pairs.has(name); }
    forEach(callback) { this.pairs.forEach((value, name) => callback(value, name)); }
  }

  const fetchStub = (url) => { posts.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };

  new Function('document', 'fetch', 'FormData', 'navigator', 'window', 'CustomEvent', source)(
    document, fetchStub, StubFormData, { sendBeacon() { return true; } },
    { addEventListener() {} }, class CustomEvent { constructor(type) { this.type = type; } },
  );

  /** One control of the form: its type, the row it sits in, and the events it may fire. */
  const control = (options) => {
    const target = {
      name: options.name || 'f.sites.guards.branding.title',
      type: options.type || 'text',
      checked: options.checked === undefined ? true : options.checked,
      matches: (sel) => Boolean(options.painted) && String(sel).indexOf(options.painted) >= 0,
      closest: (sel) => (sel === '.sc-brand-row' && options.row ? row : null),
    };
    Object.defineProperty(target, 'value', {
      get() { return fields.get(target.name); },
      set(value) { fields.set(target.name, value); },
    });
    return target;
  };

  const fire = (type, target) => (handlers[type] || []).forEach((fn) => fn({ target }));
  const settle = () => new Promise((resolve) => setTimeout(resolve, 700));
  const report = () => ({ posts: posts.length, reloads: counters.reloads,
                          src_changed: frame.currentSrc !== startSrc });

  return { control, fire, settle, report, row };
}

(async () => {
  const out = {};

  /* the mark's placement: pressed, painted in the frame already — one draft, no reload */
  {
    const run = mount();
    const target = run.control({ name: 'f.sites.guards.branding.mark_side',
                                 painted: 'input[data-sc-mark-side-option]' });
    run.fire('input', target);
    run.fire('change', target);
    await run.settle();
    out.mark_side = run.report();
  }

  /* a palette tile: pressed, painted into the frame from the tile's own sheet — one draft, no reload */
  {
    const run = mount();
    const target = run.control({ name: 'f.sites.guards.theme.preset', type: 'radio',
                                 painted: 'input[data-sc-preset-sheet]' });
    target.value = 'parchment';
    run.fire('input', target);
    run.fire('change', target);
    await run.settle();
    out.palette_press = run.report();
  }

  /* a text field the frame holds itself: typed, then left — one draft, one reload */
  {
    const run = mount();
    const target = run.control({ name: 'f.sites.guards.branding.title' });
    target.value = 'Acme Wing';
    run.fire('input', target);
    run.fire('change', target);
    await run.settle();
    out.text_field = run.report();
  }

  /* a SERVER-DRAWN row (`branding.welcome`): typed — the box follows on the keystroke, not on blur */
  {
    const run = mount();
    const target = run.control({ name: 'f.sites.guards.branding.welcome', row: true });
    target.value = 'Our servers, our pilots.';
    run.fire('input', target);
    await run.settle();
    out.server_drawn_row_typing = run.report();
    /* …and leaving the field adds nothing: the frame already carries what the last draft posted */
    run.fire('change', target);
    await run.settle();
    out.server_drawn_row_blur = run.report();
  }

  /* a switch whose change must repaint the frame (a section switched off drops its page) */
  {
    const run = mount();
    const target = run.control({ name: 'f.sites.guards.sections.community', type: 'checkbox',
                                 checked: false });
    target.value = '0';
    run.fire('input', target);
    run.fire('change', target);
    await run.settle();
    out.section_toggle = run.report();
  }

  process.stdout.write(JSON.stringify(out));
})();
