/* Drives the shipped `static/site-config.js` under a stub DOM and reports what it puts in the "Add a
 * site" form's NAME field as the role changes. The real file is executed; only its surroundings are
 * stand-ins.
 *
 *     node site_add_harness.js <path to site-config.js>
 *
 * Prints one JSON object: {"<scenario>": "<value in the name field>", ...}
 */
'use strict';

const fs = require('fs');

const scriptPath = process.argv[2];
if (!scriptPath) {
  console.error('usage: node site_add_harness.js <site-config.js>');
  process.exit(2);
}
const source = fs.readFileSync(scriptPath, 'utf8');

/** One load of the shipped file against a fresh add form; `steps` runs as the operator. */
function scenario(role, existing, steps) {
  const roleField = { value: role, listeners: [] };
  const nameField = { value: existing, listeners: [] };
  roleField.addEventListener = (type, fn) => roleField.listeners.push(fn);
  nameField.addEventListener = (type, fn) => nameField.listeners.push(fn);

  const form = {
    querySelector(sel) {
      if (sel === 'select[name="_role"]') { return roleField; }
      if (sel === 'input[name="_key"]') { return nameField; }
      return null;
    },
  };
  const document = {
    addEventListener() {},
    querySelectorAll() { return []; },
    querySelector(sel) { return sel === 'form.sc-addform' ? form : null; },
  };

  new Function('document', 'fetch', 'FormData', 'navigator', 'window', 'CustomEvent', source)(
    document, () => Promise.resolve({ ok: true }), class {}, { sendBeacon() { return true; } },
    { addEventListener() {} }, class {},
  );
  (steps || []).forEach((step) => {
    if (step.role !== undefined) {
      roleField.value = step.role;
      roleField.listeners.forEach((fn) => fn({}));
    }
    if (step.type !== undefined) {
      nameField.value = step.type;
      nameField.listeners.forEach((fn) => fn({ target: nameField }));
    }
  });
  return nameField.value;
}

const out = {
  root_at_load: scenario('', ''),
  root_picked: scenario('', '', [{ role: '' }]),
  group_at_load: scenario('5th Guards', ''),
  group_switched: scenario('', '', [{ role: '5th Guards' }, { role: 'Other Token' }]),
  accents_and_punctuation: scenario('', '', [{ role: 'Élite & Co.' }]),
  typed_name_survives: scenario('', '', [{ type: 'my-own' }, { role: '5th Guards' }]),
  rendered_name_survives: scenario('5th Guards', 'custom'),
  proposal_then_switch: scenario('5th Guards', '5th-guards', [{ role: 'Other Token' }]),
};

process.stdout.write(JSON.stringify(out));
