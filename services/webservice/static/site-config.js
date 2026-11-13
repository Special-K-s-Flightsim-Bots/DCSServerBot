/* site-config.js — the Site settings page's enhancements: the expert sheet's editor and live check,
 * Expert Mode as a view control, each card's own preview, and the character card's two lists.
 *
 * THE EXPERT SHEET. The form's control is a textarea, so a page with scripts off posts a sheet and
 * the save still refuses an invalid one. This script puts a CodeMirror instance (the vendored CSS
 * mode) over the same text: the sheet is highlighted, a check runs as the operator types and on
 * `Check`, and the answer is drawn twice — the marked line in the gutter and across it, and the
 * route's own sentence in the verdict slot. It writes nothing and decides nothing: the route runs the
 * same validator the save runs, so a verdict that says "ok" is one the save accepts.
 *
 * THE PREVIEW. Each card mounts its site's own document once, in the panel beside its editing
 * column, and this script writes the operator's values into that document's own DOM. Each block's row
 * states the element it drives in `data-sc-drives` (the site's own class names) and how to write it
 * in `data-sc-kind`; the selectors are read from the markup, never kept here, so the row, its
 * sentence and the binding have one owner. Nothing is saved: the box follows `input` events, and Save
 * is the only write.
 *
 * It carries no user-visible string: the verdict is the route's own sentence, and every word in the
 * box is the site's.
 */
(() => {
  // a typed entry's address is bound straight into the preview's document, which shares this page's
  // origin: only the three shapes the parser accepts become an href, so a `javascript:` value cannot
  // become a live link.
  const SAFE_HREF = /^(https?:\/\/|mailto:|\/)/i;

  const paint = (doc, row) => {
    const selector = row.getAttribute('data-sc-drives');
    if (!selector) {
      return;
    }
    const target = doc.querySelector(selector);
    if (!target) {
      return;
    }
    const kind = row.getAttribute('data-sc-kind') || 'text';
    if (kind === 'order') {
      /* THE BAR'S NAV, in the page's own row order. The block carries each id's address once
       * (`data-sc-order`), so the anchors are matched by `href`, never by their text — the site's
       * labels are the site's own words. A row switched off is hidden here because the next render
       * omits it: the frame has no way to drop it, and hiding it says the same thing. */
      let addresses;
      try {
        addresses = JSON.parse(row.getAttribute('data-sc-order') || '{}');
      } catch (error) {
        return;
      }
      const anchors = [...target.querySelectorAll('a')];
      row.querySelectorAll('[data-reorder-row]').forEach((entry) => {
        const anchor = anchors.find((link) =>
          link.getAttribute('href') === addresses[entry.getAttribute('data-reorder-id')]);
        if (!anchor) {
          return;
        }
        const box = entry.querySelector('input[type="checkbox"]');
        anchor.hidden = Boolean(box && !box.checked);
        target.appendChild(anchor);
      });
      return;
    }
    if (kind === 'links') {
      target.querySelectorAll('a').forEach((link) => link.remove());
      row.querySelectorAll('[data-cfg-row]').forEach((entry) => {
        const label = entry.querySelector('input[name$=".label"]');
        if (!label || !label.value.trim()) {
          return;
        }
        const address = entry.querySelector('input[name$=".href"]');
        const link = doc.createElement('a');
        link.textContent = label.value.trim();
        if (address && SAFE_HREF.test(address.value.trim())) {
          link.setAttribute('href', address.value.trim());
        }
        target.appendChild(link);
      });
      return;
    }
    const field = row.querySelector('input, textarea, select');
    if (!field) {
      return;
    }
    // what the site itself rendered, kept so emptying a field restores it: an empty value is not a
    // blank line on the site — the render falls back (a community name to the guild, then the title)
    if (target.dataset.scOrig === undefined) {
      target.dataset.scOrig = target.textContent;
    }
    const value = field.value.trim();
    if (!value) {
      target.textContent = target.dataset.scOrig;
      return;
    }
    target.textContent = value;
  };

  /* ONE FRAME PER CARD. The page mounts one site document per card, beside that card's editing
   * column, not inside a block — so there may be SEVERAL frames on one page, and each is resolved
   * from its OWN form and bound to it alone. A single global frame would leave every card but the
   * first writing into a document the operator is not looking at. Every `data-sc-drives` row of
   * every block of a card writes into that card's document.
   *
   * A frame may also be mounted AFTER load: a site the operator switches on has no frame until they
   * flip its switch, and the draft's own script mounts it. That script reports the new frame with
   * `sc-preview-mounted` on its panel, which bubbles here — so the binding is delegated and a frame
   * that appears later is bound exactly like one the server rendered. */
  const bindPreview = (frame) => {
    if (frame.dataset.scBound) {
      return;
    }
    frame.dataset.scBound = '1';
    const scope = frame.closest('form') || document;
    const bind = () => {
      const doc = frame.contentDocument;
      if (!doc) {
        return;
      }
      scope.querySelectorAll('[data-sc-drives]').forEach((row) => paint(doc, row));
      // the hero's mark side: the pills name the placement, and the preview's own `.in` carries the
      // class. Only a hero that already draws a mark has one to move.
      const chosen = scope.querySelector('input[data-sc-mark-side-option]:checked');
      const hero = doc.querySelector('.hero .in.has-mark');
      if (hero && chosen) {
        hero.classList.toggle('mark-left', chosen.value === 'left');
        hero.classList.toggle('mark-right', chosen.value === 'right');
      }
    };
    frame.addEventListener('load', bind);
    scope.addEventListener('input', bind);
    scope.addEventListener('change', bind);
    /* a dragged row is a MOVE, not an edit: `static/reorder.js` reports it with one bubbling
     * `reorder-moved`, which is what repaints the frame's own order before Save */
    scope.addEventListener('reorder-moved', bind);
    if (frame.contentDocument && frame.contentDocument.readyState === 'complete') {
      bind();
    }
  };

  document.querySelectorAll('iframe[data-sc-preview]').forEach(bindPreview);
  document.addEventListener('sc-preview-mounted', (event) => {
    const panel = event.target;
    const frame = panel && panel.querySelector && panel.querySelector('iframe[data-sc-preview]');
    if (frame) {
      bindPreview(frame);
    }
  });
})();

(() => {
  /* THE CARD THE URL NAMES. A save answers with the page's address carrying its OWN site, and the
   * page opens that card; this brings it into view, because a page that has scrolled back to the top
   * after a save reads as "it saved the wrong site" — the first card's switch is the one on screen.
   * The name is compared against the card's own attribute, never built into a selector. */
  let wanted = '';
  try {
    wanted = new URLSearchParams(window.location.search).get('site') || '';
  } catch (error) {
    return;
  }
  if (!wanted) {
    return;
  }
  const card = Array.prototype.slice
    .call(document.querySelectorAll('details[data-sc-card]'))
    .find((node) => node.getAttribute('data-sc-card') === wanted);
  if (card && typeof card.scrollIntoView === 'function') {
    card.scrollIntoView({block: 'start'});
  }
})();

(() => {
  /* THE CARD'S DRAFT. The box has to follow the values in the form BEFORE Save, and the values this
   * script cannot paint itself — the nav's list, the tile strip, the character's bands, a palette —
   * are the server's to render. So the card posts its own form to the draft door, debounced, and the
   * route keeps that one block in this session's memory. Nothing is written by it: the draft is what
   * the preview renders, and Save is still the only write. The route answers a no-op success, and the
   * frame is reloaded from it — paying one request per discrete change, never per keystroke.
   *
   * A SITE THAT IS SWITCHED OFF HAS NO FRAME until its switch is flipped, because a frame of an
   * address that answers 404 draws the framework's own refusal. Switching it on therefore posts the
   * draft (with `enabled`) FIRST and mounts the frame only once the route has it; switching it off
   * puts the server's own off-state sentence back. The words and both addresses come from the
   * panel's attributes, which the page module rendered and translated for this request. */
  const DEBOUNCE_MS = 400;
  document.querySelectorAll('aside[data-sc-site]').forEach((panel) => {
    const form = panel.closest('form');
    const url = panel.getAttribute('data-sc-draft-url') || '';
    const src = panel.getAttribute('data-sc-preview-url') || '';
    const clearField = panel.getAttribute('data-sc-draft-clear') || '';
    const title = panel.getAttribute('data-sc-preview-title') || '';
    const offWords = panel.getAttribute('data-sc-preview-off') || '';
    if (!form || !url) {
      return;
    }
    const card = panel.closest('details');
    let timer = null;

    const frameNow = () => panel.querySelector('iframe[data-sc-preview]');
    const box = () => panel.querySelector('.sc-pv-box');
    const body = () => {
      const data = new FormData(form);
      /* A PICKED FILE IS NOT A FIELD: the mark and the sheet have their own doors, and posting their
       * bytes here would spend the body bound on something this route never reads. */
      form.querySelectorAll('input[type="file"]').forEach((input) => data.delete(input.name));
      return data;
    };
    const post = (extra) => {
      const data = body();
      Object.keys(extra || {}).forEach((name) => data.set(name, extra[name]));
      return fetch(url, {method: 'POST', body: data, credentials: 'same-origin'})
        .then((reply) => reply.ok)
        .catch(() => false);
    };
    const stamp = () => Date.now();
    const address = () => src + (src.indexOf('?') >= 0 ? '&' : '?') + '_=' + stamp();
    const reload = () => {
      const frame = frameNow();
      if (!frame || !src) {
        return;
      }
      frame.src = address();
    };
    const draft = async (repaint) => {
      const stored = await post(null);
      if (stored && repaint) {
        reload();
      }
      return stored;
    };
    const queue = (repaint) => {
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(() => {
        timer = null;
        draft(repaint);
      }, DEBOUNCE_MS);
    };
    const mount = async () => {
      const target = box();
      if (!target || frameNow() || !src) {
        /* NO DOCUMENT TO MOUNT. A site the file added has no address of its own yet, and the card
         * points the box at the console's demo document instead — so `src` always answers here,
         * and only a panel rendered without one stays empty. */
        return;
      }
      await post(null);
      const frame = document.createElement('iframe');
      frame.setAttribute('title', title);
      frame.setAttribute('tabindex', '-1');
      frame.setAttribute('data-sc-preview', '');
      frame.src = address();
      target.className = 'sc-pv-box';
      target.removeAttribute('role');
      target.removeAttribute('data-sc-pv-off');
      target.replaceChildren(frame);
      panel.dispatchEvent(new CustomEvent('sc-preview-mounted', {bubbles: true}));
    };
    const unmount = async () => {
      await post(null);
      const target = box();
      if (!target) {
        return;
      }
      target.replaceChildren(document.createTextNode(offWords));
      target.className = 'sc-pv-box sc-pv-off';
      target.setAttribute('role', 'status');
    };
    /* LEAVING THE CARD DISCARDS IT. `pagehide` covers a reload, a link and a closed tab; the card's
     * own toggle covers closing it. `sendBeacon` is the one post a departing page can still make, and
     * the token travels in the body it already carries. */
    const discard = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (!clearField) {
        return;
      }
      const data = body();
      data.set(clearField, '1');
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, data);
      } else {
        fetch(url, {method: 'POST', body: data, credentials: 'same-origin', keepalive: true});
      }
    };

    const toggle = (card || form).querySelector('input[role="switch"][name$=".enabled"]');
    if (toggle) {
      toggle.addEventListener('change', () => {
        if (toggle.checked) {
          mount();
        } else {
          unmount();
        }
      });
    }
    /* AN EDIT IS A NAMED CONTROL: the card also carries view controls (the Expert Mode box, a confirm
     * button), and those move on screen without changing a value the save would post. A control with
     * no `name` posts nothing, so it starts no draft either. */
    const edited = (event) => {
      const target = event && event.target;
      return Boolean(target && typeof target.name === 'string' && target.name);
    };
    /* THE MARK'S PLACEMENT IS ALREADY PAINTED. Its pills move the hero's own class in the frame the
     * moment they are pressed (see `bindPreview`), so reloading the pane would redraw the whole site
     * a second time to show a class that is already there. The draft is still posted — what the pane
     * renders next time must carry the new side — and only the repaint is skipped. */
    const painted = (event) => Boolean(event && event.target && event.target.matches
                                       && event.target.matches('input[data-sc-mark-side-option]'));
    form.addEventListener('input', (event) => {
      if (edited(event)) {
        queue(false);
      }
    });
    form.addEventListener('change', (event) => {
      if (edited(event)) {
        queue(!painted(event));
      }
    });
    form.addEventListener('reorder-moved', () => queue(true));
    form.addEventListener('submit', () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    });
    window.addEventListener('pagehide', discard);
    if (card) {
      card.addEventListener('toggle', () => {
        if (!card.open) {
          discard();
        }
      });
    }
  });
})();

(() => {
  /* THE CHARACTER CARD. A press rewrites the two lists that sit under the pills, the sentence saying
   * what the character means, the two "why" notes and the card's own chip — and NOTHING leaves the
   * page: Save is the only write, and the switches are the ordinary posted controls.
   *
   * The table is the page module's (one home, rendered once into the JSON this reads); the script
   * keeps no second copy of a set and no sentence of its own. Every word it prints comes from the
   * payload, which the server translated for this request.
   *
   * A switch moved by hand afterwards is kept: nothing re-applies a set on load, and the line the
   * card shows names the divergence instead of reverting the operator's work. */
  const fill = (template, values) => String(template).replace(/%\((\w+)\)s/g, (whole, key) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : whole);

  document.querySelectorAll('[data-sc-character-sets]').forEach((payload) => {
    const card = payload.closest('details') || payload.closest('form') || document;
    let data;
    try {
      data = JSON.parse(payload.getAttribute('data-sc-character-sets') || '');
    } catch (error) {
      return;
    }
    const pills = [...card.querySelectorAll('input[data-sc-character]')];
    const sections = [...card.querySelectorAll('input[data-sc-section]')];
    const metrics = [...card.querySelectorAll('input[data-sc-metric]')];
    const what = card.querySelector('[data-sc-character-what]');
    const whySections = card.querySelector('[data-sc-why-sections]');
    const whyMetrics = card.querySelector('[data-sc-why-metrics]');
    const changed = card.querySelector('[data-sc-character-changed]');
    const changedText = card.querySelector('[data-sc-character-changed-text]');
    const chip = card.querySelector('[data-sc-character-chip]');
    const count = card.querySelector('[data-sc-character-count]');
    if (!pills.length || !data.sets) {
      return;
    }

    const label = (ident) => (data.labels && data.labels[ident]) || ident;
    const pressed = () => {
      const box = pills.find((pill) => pill.checked);
      return box ? box.value : '';
    };
    const paintRow = (box, on) => {
      box.checked = on;
      const row = box.closest('[data-sc-row]');
      if (row) {
        row.classList.toggle('off', !on);
      }
    };
    const paintNote = (node, text) => {
      if (!node) {
        return;
      }
      node.textContent = text || '';
      node.hidden = !text;
    };
    /* the chip counts the switches AS THEY STAND: a press rewrites them, so it moves with the press,
     * and a hand edit moves it too — the header can never state a count the lists do not have */
    const paintCount = () => {
      if (!count) {
        return;
      }
      count.textContent = fill(data.count, {
        sections: sections.filter((box) => box.checked).length,
        sectionsTotal: data.sectionsTotal,
        metrics: metrics.filter((box) => box.checked).length,
        metricsTotal: data.metricsTotal,
      });
    };
    const paint = (ident) => {
      const spec = data.sets[ident];
      if (!spec) {
        return;
      }
      const onSections = new Set(spec.sections);
      sections.forEach((box) => paintRow(box, onSections.has(box.getAttribute('data-sc-section'))));
      const onMetrics = new Set(spec.metrics);
      metrics.forEach((box) => paintRow(box, onMetrics.has(box.getAttribute('data-sc-metric'))));
      if (what) {
        what.textContent = spec.what;
      }
      paintNote(whySections, spec.whySections);
      paintNote(whyMetrics, spec.whyMetrics);
      if (chip) {
        chip.textContent = label(ident);
      }
      paintCount();
      if (changed) {
        changed.hidden = true;
      }
    };

    const diverged = () => {
      const spec = data.sets[pressed()];
      if (!spec) {
        return false;
      }
      const shown = (boxes, key) => boxes.filter((box) => box.checked)
        .map((box) => box.getAttribute(key));
      const same = (have, want) => have.length === want.length && have.every((id) => want.includes(id));
      return !same(shown(sections, 'data-sc-section'), spec.sections)
        || !same(shown(metrics, 'data-sc-metric'), spec.metrics);
    };

    /* a checked radio fires no `change` when it is pressed again, and that press is exactly what
     * clears the divergence line — so the press is read from `click`, and `change` covers the
     * keyboard's arrow keys. Both handlers are idempotent. */
    const press = (pill) => {
      if (pill.checked) {
        paint(pill.value);
      }
    };
    pills.forEach((pill) => {
      pill.addEventListener('click', () => press(pill));
      pill.addEventListener('change', () => press(pill));
    });

    [...sections, ...metrics].forEach((box) => box.addEventListener('change', () => {
      paintRow(box, box.checked);
      paintCount();
      if (!changed) {
        return;
      }
      const differs = diverged();
      changed.hidden = !differs;
      if (differs && changedText) {
        const name = document.createElement('b');
        name.textContent = label(pressed());
        const parts = String(data.changed || '').split('%(name)s');
        changedText.replaceChildren(document.createTextNode(parts[0]), name,
                                    document.createTextNode(parts[1] || ''));
      }
    }));
  });
})();

(() => {
  /* §E — THE MARK'S REMOVAL. The visible Remove posts to the removal route on its own, so the page
   * works with scripts off: that plain submit IS the no-script path, and nothing here decides
   * whether a removal happens. With scripts on it asks once, INLINE — the actions row steps aside
   * and the confirm takes its place — because removing an operator's own upload is not a stray
   * press. The confirm's own button is a submit of the same form, so it carries the same token. */
  /* §E — THE PICKER AND ITS CONFIRM ARE ONE ANATOMY, SHARED. The mark's slot and the palette block's
   * uploaded-sheet row draw the same three parts — a clipped native file input, the console's own
   * label button, and the line that states the chosen file — so this script binds BOTH through the
   * two slot attributes rather than keeping a second copy per door. Nothing here decides what a post
   * writes: the input's own form and the buttons' `formaction` are the controls. */
  document.querySelectorAll('[data-sc-mark-remove]').forEach((button) => {
    const slot = button.closest('[data-sc-mark], [data-sc-sheet]');
    const confirm = slot && slot.querySelector('[data-sc-mark-confirm]');
    const acts = slot && slot.querySelector('[data-sc-mark-acts]');
    if (!confirm || !acts) {
      return;
    }
    button.addEventListener('click', (event) => {
      event.preventDefault();
      acts.hidden = true;
      confirm.hidden = false;
    });
    const cancel = confirm.querySelector('[data-sc-mark-cancel]');
    if (cancel) {
      cancel.addEventListener('click', () => {
        confirm.hidden = true;
        acts.hidden = false;
      });
    }
  });
})();

(() => {
  /* §E — THE MARK'S PICKER. The native file input is clipped, not removed: it posts, and with
   * scripts off it is the only control. On screen the console's own button opens it, and the line
   * above the buttons states the CHOSEN file's name from this script — before a choice it keeps the
   * server's sentence about what the page accepts, so the browser's own "no file chosen" is never
   * read out. The sentence is the server's (translated for this request); the name is the file's. */
  document.querySelectorAll('[data-sc-file-status]').forEach((status) => {
    const slot = status.closest('[data-sc-mark], [data-sc-sheet]');
    const input = slot && slot.querySelector('input.sc-av-file');
    if (!input) {
      return;
    }
    const idle = status.textContent;
    const pattern = status.getAttribute('data-sc-file-chosen') || '%(name)s';
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      status.textContent = file ? pattern.split('%(name)s').join(file.name) : idle;
    });
  });
})();

(() => {
  /* THE PRESET GALLERY. Every tile carries its own badge and the server sets the pressed state from
   * what the file stores; a press repaints BOTH from the radio the browser just checked, so the
   * choice is on screen before Save — the same immediate truth the character pills show. Nothing is
   * posted, and nothing here decides what the save writes: the radio is the control. */
  document.querySelectorAll('.sc-gallery').forEach((gallery) => {
    const tiles = [...gallery.querySelectorAll('[data-sc-preset-tile]')];
    if (!tiles.length) {
      return;
    }
    const paint = () => tiles.forEach((tile) => {
      const radio = tile.querySelector('input[type="radio"]');
      const on = Boolean(radio && radio.checked);
      tile.classList.toggle('on', on);
      const badge = tile.querySelector('[data-sc-preset-badge]');
      if (badge) {
        badge.hidden = !on;
      }
    });
    tiles.forEach((tile) => {
      const radio = tile.querySelector('input[type="radio"]');
      if (!radio) {
        return;
      }
      /* a checked radio fires no `change` when it is pressed again, and `change` covers the
       * keyboard's arrow keys — both handlers are idempotent */
      radio.addEventListener('click', paint);
      radio.addEventListener('change', paint);
    });
    paint();
  });
})();

(() => {
  /* THE PALETTE BLOCK PAINTS THE CARD'S OWN FRAME, before Save. Two controls name a palette and
   * neither posts: a preset tile carries its sheet's own declaration map and scheme as
   * `data-sc-preset-sheet` (read server-side from the preset's file), and the expert sheet's
   * textarea carries declarations in the grammar `SC_PALETTE.parseSheet` reads. Both write through
   * the wizard's ONE paint, so a press lands on the frame exactly as a derivation does.
   *
   * A sheet line the grammar does not describe is skipped and nothing is removed, so the frame keeps
   * the last state it was given rather than emptying. The deprecated `theme.light` switch is not a
   * palette, so it paints nothing. */
  document.querySelectorAll('section[data-sc-block="palette"]').forEach((block) => {
    const card = block.closest('form') || document;
    /* LOOKED UP WHEN IT PAINTS, never captured: a card whose site was switched on mounts its frame
     * after load, and a capture taken here would paint into nothing for the rest of the visit. */
    const frameNow = () => card.querySelector('iframe[data-sc-preview]');
    const sheet = card.querySelector('[data-sc-x-text]');
    const scheme = block.getAttribute('data-sc-pal-scheme') || '';
    const paint = (values, to) => {
      const palette = window.SC_PALETTE;
      const frame = frameNow();
      const doc = frame && frame.contentDocument;
      if (!palette || !palette.paintRoot || !doc || !doc.documentElement || !values) {
        return;
      }
      palette.paintRoot(doc, values, to);
    };
    block.querySelectorAll('[data-sc-preset-tile]').forEach((tile) => {
      const radio = tile.querySelector('input[type="radio"]');
      if (!radio) {
        return;
      }
      /* a checked radio fires no `change` when it is pressed again, and `change` covers the
       * keyboard's arrow keys — both handlers are idempotent */
      const press = () => {
        if (!radio.checked) {
          return;
        }
        let spec;
        try {
          spec = JSON.parse(radio.getAttribute('data-sc-preset-sheet') || '{}');
        } catch (error) {
          return;
        }
        paint(spec.declarations, spec.scheme);
      };
      radio.addEventListener('click', press);
      radio.addEventListener('change', press);
    });
    if (!sheet) {
      return;
    }
    const paintSheet = () => {
      const palette = window.SC_PALETTE;
      if (!palette || !palette.parseSheet) {
        return;
      }
      const declarations = palette.parseSheet(sheet.value);
      if (!Object.keys(declarations).length) {
        return;
      }
      paint(declarations, scheme);
    };
    /* the textarea's own `input` is the no-editor path; the editor over it reports the operator's
     * keystroke as `sc-sheet-edited` on the same element, because a hidden textarea gets none */
    sheet.addEventListener('input', paintSheet);
    sheet.addEventListener('sc-sheet-edited', paintSheet);
  });
})();

(() => {
  /* §D — THE EXPERT SHEET. The form's control is the TEXTAREA, so a page with scripts off posts a
   * sheet and the save still refuses an invalid one. This block puts a CodeMirror instance over the
   * same text with the vendored CSS mode: the sheet's declarations are highlighted, and the line the
   * validator names is marked in the gutter AND across the line — the refusal's own sentence (the
   * server's, with its line and its token) is printed in the verdict slot under the sheet.
   *
   * THE VERDICT SLOT HAS A FIXED HEIGHT and nothing here may move the caret: a check runs while the
   * operator is still typing, so `setCursor` would hijack the next keystrokes into whatever line the
   * error landed on, and the document would be corrupted by the act of fixing it.
   *
   * THE COPY IS NAMED IN THE BAR, NEVER IN THE TEXT — a marker comment in the sheet would itself be
   * refused by the validator. The sheet starts as the shipped sheet's transcription; the moment it
   * differs the badge reads `edited` and `Load the shipped sheet` becomes the restore control. */
  const DEBOUNCE_MS = 250;
  const REFUSED = ['sc-refused', 'sc-refused-wash'];

  /* the served sentence patterns are gettext msgids with `%(name)s` holes: the translator owns the
   * words, the page owns the numbers */
  const fill = (pattern, values) => {
    let text = pattern || '';
    Object.keys(values).forEach((key) => {
      text = text.split('%(' + key + ')s').join(String(values[key]));
    });
    return text;
  };

  /* a bound read the way the card states it: `8 192`, not `8192` */
  const thousands = (value) => String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');

  const declarations = (text) => text.split('\n').filter((line) => line.trim()).length;

  document.querySelectorAll('[data-sc-expert]').forEach((block) => {
    let data;
    try {
      data = JSON.parse(block.getAttribute('data-sc-expert') || '{}');
    } catch (error) {
      return;
    }
    const text = block.querySelector('[data-sc-x-text]');
    const badge = block.querySelector('[data-sc-x-badge]');
    const when = block.querySelector('[data-sc-x-when]');
    const counter = block.querySelector('[data-sc-x-count]');
    const verdict = block.querySelector('[data-sc-x-verdict]');
    const load = block.querySelector('[data-sc-x-load]');
    const checkButton = block.querySelector('[data-sc-x-check]');
    if (!text || !badge || !when || !counter || !verdict || !load || !checkButton) {
      return;
    }
    const prefill = data.prefill || '';
    const start = text.value || prefill;
    let editor = null;
    let timer = null;
    let marks = [];

    const current = () => (editor ? editor.getValue() : text.value);
    const isCopy = () => current().trim() === prefill.trim();

    const paintState = () => {
      const copy = isCopy();
      const fromWizard = block.dataset.scXFrom === 'wizard';
      /* SHOWN IS NOT STORED: when the carrier is empty and the site stores no sheet, the box's text
       * is the shipped transcription on loan for reading. The badge and the line say so; the moment
       * a keystroke fills the carrier the copy is the operator's and the ordinary states resume. */
      const shown = copy && !data.stored && !text.value.trim();
      badge.textContent = fromWizard ? (data.badgeWizard || '')
        : (shown ? (data.badgeUnstored || '') : (copy ? (data.badgeCopy || '')
                                                      : (data.badgeEdited || '')));
      badge.classList.toggle('default', copy && !fromWizard);
      load.disabled = copy;
      const value = current();
      when.textContent = fromWizard ? (data.whenWizard || '')
        : fill(shown ? data.whenUnstored : data.when,
               {declarations: declarations(value), lines: value.split('\n').length});
      counter.textContent = fill(data.count, {allowed: thousands(data.limit || 0),
                                              used: thousands(value.length)});
    };

    const clearMarks = () => {
      if (!editor) {
        return;
      }
      marks.forEach((line) => {
        editor.removeLineClass(line, 'gutter', REFUSED[0]);
        editor.removeLineClass(line, 'background', REFUSED[1]);
      });
      marks = [];
    };

    const apply = (found) => {
      clearMarks();
      if (found && found.ok) {
        verdict.textContent = fill(data.accepted, {declarations: declarations(current()),
                                                   characters: thousands(current().length)});
        verdict.className = 'sc-verdict ok';
        verdict.dataset.state = 'ok';
        return;
      }
      verdict.textContent = (found && found.message) || '';
      verdict.className = 'sc-verdict bad';
      verdict.dataset.state = 'bad';
      const line = found && found.line;
      if (!editor || !line || line < 1 || line > editor.lineCount()) {
        return;
      }
      editor.addLineClass(line - 1, 'gutter', REFUSED[0]);
      editor.addLineClass(line - 1, 'background', REFUSED[1]);
      marks.push(line - 1);
    };

    const check = async () => {
      const body = new FormData();
      body.append(block.dataset.scXCsrf, block.dataset.scXToken || '');
      body.append(block.dataset.scXField, current());
      try {
        const reply = await fetch(block.dataset.scXUrl, {
          method: 'POST', body, credentials: 'same-origin',
        });
        apply(await reply.json());
      } catch (error) {
        /* a network hiccup: no verdict, and no claim. The save validates server-side either way. */
      }
    };

    const setValue = (value) => {
      if (editor) {
        editor.setValue(value);
      } else {
        text.value = value;
      }
    };

    const onChange = (instance, change) => {
      /* THE CARRIER IS THE OPERATOR'S, NOT THE SURFACE'S. A change the surface made by itself
       * (`setValue`, `refresh`, a programmatic `setDoc`) is not an edit: syncing it would turn the
       * shipped transcription into this site's sheet without a keystroke. Only a change the operator
       * made — typing, pasting, undoing, dropping — fills the carrier. */
      if (change && change.origin && !/^(\+input|paste|cut|drag|drop|undo|redo|delete)/.test(change.origin)) {
        return;
      }
      text.value = current();
      /* the palette block repaints the card's frame from this same text: a keystroke that reached
       * the editor reached no `input` on the textarea it hides */
      text.dispatchEvent(new CustomEvent('sc-sheet-edited', {bubbles: true}));
      if (block.dataset.scXFrom) {
        delete block.dataset.scXFrom;
      }
      paintState();
      if (block.dataset.scXQuiet) {
        return;
      }
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(check, DEBOUNCE_MS);
    };

    if (window.CodeMirror) {
      const host = document.createElement('div');
      host.className = 'sc-sheet-host';
      text.parentNode.insertBefore(host, text);
      editor = window.CodeMirror(host, {
        value: start, mode: 'css', lineNumbers: true, lineWrapping: false,
        tabSize: 2, indentUnit: 2,
      });
      text.hidden = true;
      editor.on('change', onChange);
      /* a card is a <details>: an editor built inside a CLOSED one measured a zero-width gutter and
       * an empty viewport, so opening the card re-measures it */
      const card = block.closest('details');
      if (card) {
        card.addEventListener('toggle', () => editor.refresh());
      }
    } else {
      text.addEventListener('input', onChange);
    }

    checkButton.addEventListener('click', check);
    /* THE SHEET IS BUILT WHILE IT MAY BE HIDDEN (Expert Mode is closed on a site with no sheet), so
     * the one thing a reveal must do is re-measure: an editor built in a hidden host knows neither
     * its gutter nor its viewport. */
    block.addEventListener('sc-expert-shown', () => {
      if (editor) {
        editor.refresh();
      }
    });
    /* THE WIZARD'S HANDOFF. The palette wizard owns the derivation and this block owns the sheet, so
     * the one thing that crosses between them is the TEXT: the wizard dispatches it, this sets the
     * editor, marks the copy as the wizard's and repaints the bar. No request, no verdict of its own —
     * the operator's Check and the server's validator still decide. */
    block.addEventListener('sc-sheet', (event) => {
      /* the write is not an edit: it is the whole of the new sheet, so it revalidates nothing and
       * starts no request — the verdict the wizard prints is its own answer, and Check re-measures */
      block.dataset.scXQuiet = '1';
      setValue(String(event.detail == null ? '' : event.detail));
      text.value = current();
      delete block.dataset.scXQuiet;
      block.dataset.scXFrom = 'wizard';
      clearMarks();
      verdict.textContent = '';
      verdict.className = 'sc-verdict';
      delete verdict.dataset.state;
      paintState();
    });
    load.addEventListener('click', () => {
      if (load.disabled) {
        return;
      }
      const question = data.replace || '';
      if (window.confirm && !window.confirm(question)) {
        return;
      }
      setValue(prefill);
      text.value = prefill;
      paintState();
      check();
    });

    paintState();
  });
})();

(() => {
  /* §4 · EXPERT MODE IS A VIEW CONTROL AND NEVER THE CARRIER. The form's control is the TEXTAREA: it
   * is always in the form and always holds the sheet, whether the box is ticked, unticked, or the
   * page has no script at all — CodeMirror is only a surface over that text and leaves the textarea
   * in the form. Unticking therefore HIDES the sheet on screen and writes nothing: blanking the
   * carrier here would delete a site's palette because the operator closed a panel he had only
   * looked at. The closed state states the sheet instead — a dashed strip where the sheet would be,
   * saying how much is stored — and a site with no sheet says that in words.
   *
   * The box is ticked exactly when the site's palette IS an expert sheet; the server draws that.
   */
  document.querySelectorAll('[data-sc-expert-toggle]').forEach((box) => {
    const card = box.closest('form') || document;
    const block = card.querySelector('[data-sc-expert]');
    if (!block) {
      return;
    }
    const carrier = block.querySelector('[data-sc-x-text]');
    const stored = card.querySelector('[data-sc-expert-stored]');
    const none = card.querySelector('[data-sc-expert-none]');
    const say = card.querySelector('[data-sc-pal-say]');
    const host = card.querySelector('[data-sc-pal]');
    let payload;
    try {
      payload = JSON.parse((host && host.getAttribute('data-sc-pal')) || '{}');
    } catch (error) {
      payload = {};
    }
    const words = payload.say || {};

    const paint = () => {
      const open = box.checked;
      /* the whole sheet — its bar, its verdict and its foot — goes with the box, and the carrier
       * stays in the form: `hidden` removes nothing from the post */
      block.hidden = !open;
      const text = carrier ? carrier.value : '';
      const hasSheet = Boolean(String(text || '').trim());
      if (stored) {
        stored.hidden = open || !hasSheet;
      }
      if (none) {
        none.hidden = open || hasSheet;
      }
      if (say) {
        say.textContent = open ? (words.open || '') : (words.rest || '');
      }
      if (open) {
        block.dispatchEvent(new CustomEvent('sc-expert-shown'));
      }
    };
    box.addEventListener('change', paint);
    paint();
  });
})();

/* THE NAME FOLLOWS THE ROLE. A site's name in the file is a token, and the operator's own words —
 * "The site root", a group named "5th Guards" — are not one. So the name is proposed from the role:
 * `root` for the site root, and the role lowercased with runs of anything a name cannot carry turned
 * into one inner dash. It is a PROPOSAL: it is written only while the field holds nothing or still
 * holds the last proposal, so a name the operator typed is never overwritten under their hands. */
(() => {
  const form = document.querySelector('form.sc-addform');
  if (!form) {
    return;
  }
  const role = form.querySelector('select[name="_role"]');
  const name = form.querySelector('input[name="_key"]');
  if (!role || !name) {
    return;
  }
  const propose = () => {
    const picked = String(role.value || '');
    if (!picked) {
      return 'root';
    }
    const folded = picked.normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
    const token = folded.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    return token || 'root';
  };
  let proposed = '';
  let owned = false;
  const fill = () => {
    if (owned) {
      return;
    }
    proposed = propose();
    name.value = proposed;
  };
  /* a field that already holds something is the operator's unless it holds the proposal itself */
  if (name.value.trim() !== '') {
    owned = name.value.trim() !== propose();
  }
  fill();
  role.addEventListener('change', fill);
  name.addEventListener('input', () => { owned = name.value.trim() !== proposed; });
})();
