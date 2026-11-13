# Public site

The WebService can serve a second, player-facing site beside the admin Command Center: a read-only
statistics site for the community, optionally one view per hosting group. It shares the process, the
port and the database with the console, but not its login — every page of it is public and answers
without a session, a role or a capability. Visitors get the community's numbers, a leaderboard, a
search over the pilots and a profile per pilot; nobody can change anything from it.

The site is switched on with `frontend: true` plus a `sites:` block in `config/site.yaml` — the file
the console's **Public site** page owns. Without a served root site the console keeps the site root
and nothing about the public site is served at `/`.

## What it serves

Every address below exists in this build. `<slug>` is the site's own path segment — empty for the
site named `root`, which is served at `/` when enabled.

| Address | What it is |
|:--------|:-----------|
| `/` or `/<slug>` | The site's overview: hero figures, a live band of what is flying now, the server board, the weekly activity chart, the top pilots and — when CreditSystem has rows — the squadron standings |
| `<site>/rankings` | The leaderboard: pilots ranked by one chosen metric, filterable by server and period, every column a sort |
| `<site>/players` | Pilot search: a name search with paging, one row per pilot, each linking to the profile |
| `<site>/player/<ucid>` | One pilot's profile: totals, airframes, the servers they flew, and their other UCIDs if one Discord account links several |
| `<site>/me` | The signed-in visitor's own statistics — the same figures the profile already shows, reached from the shell's own chip |
| `<site>/search.json` | The search box's suggestions, as JSON (see [Read the numbers from the API](#read-the-numbers-from-the-api)) |
| `<site>/language` | The language switch (see [Carry the language](#carry-the-language)) |
| `<site>/theme.css` | The site's palette sheet, when one of `theme.preset`, `theme.sheet` or `theme.expert` is declared |
| `<site>/logo.png` | The site's own logo, when `branding.logo` declares a raster image |

Two addresses do **not** exist, and the site is deliberate about both:

- `<site>/console` is not a site page: the Command Center is at `/console` while a site owns the
  root, and a visitor the console would refuse gets its refusal, not a public page.
- `squadrons` is a nav id with no page in this build: the id is refused nothing at configuration
  time, but it renders no menu entry and `<site>/squads` answers `404`.

## Turning it on

Two ways in, and both write the same file:

- **In the console:** Settings → **Public site** → *Add a site*. Pick what it shows (every
  `managed_by` token `servers.yaml` names, or *The site root*), type the name it carries in the file
  and the segment it answers on, and press *Add the site*. The console appends the entry to
  `config/site.yaml` and opens that site's card.
- **By hand:** `config/site.yaml` is a small document holding one `sites:` block. This is complete
  and minimal:

```yaml
# config/site.yaml
sites:
  root: {}
```

`root: {}` is the smallest valid block: the site with no group token, served at `/`, showing the
servers that declare no `managed_by`, every page id in the menu and the shipped look. `root:` with
nothing under it is YAML `null` and is refused (`sites.root: must be a map (the site's own block)`) —
write the braces.

Both routes need `frontend: true` in `config/services/webservice.yaml`; that switch installs the web
UI, and the site block decides what it serves.

!!! note "The setup tour does not create a site"
    The tour ends with your servers configured. A public site is added afterwards — in the console,
    or by writing the file above.

!!! note "The block's older home"
    An installation that has never used the console's page may still carry `sites:` under `DEFAULT:`
    in `config/services/webservice.yaml`. That block is read **only while `config/site.yaml` does not
    exist**; the moment the file exists it is the only source, and the console warns when both carry
    a block. The Public site page shows the legacy entries, and its first save writes them to
    `config/site.yaml` — so opening the page once and saving is the migration.

!!! warning "An enabled root site moves the console"
    The Command Center moves to `/console` only while an ENABLED site is SERVED at the site root —
    a `root:` block whose `enabled` key is absent or `true`. The console keeps `/` in every other
    case:

    - no `sites:` block at all;
    - an empty block;
    - a `root:` block with `enabled: false` — a switched-off site registers no route, so it owns no
      address;
    - a block that declares only group sites (their pages live at `/<slug>`, never at `/`).

    With no enabled root site, `/` answers the console: an anonymous visitor is sent to the login
    page, and a bookmark to `/console` answers `404`.

    The sign-in page keeps its own address (`/auth/login`), and the installation's fixed surface
    never moves either: `/login`, `/logout`, `/auth/*`, `/language`, `/theme`, `/i18n/*` and
    `/static`.

    `enabled` is a STRUCTURAL key: it decides which routes exist, so it takes effect through the
    console's apply rather than through a page load. A save on the Public site page offers that
    apply, and the apply rebuilds the site table in place, so a site switched off or on takes effect
    at once — no bot restart. See [Applying the change](#applying-the-change).

## The Sites page

Settings → **Public site** is the console page that owns `config/site.yaml`. Every site is one card,
and the card's header leads with the address a visitor types — `/` for the root site, `/guards` for a
site whose slug is `guards` — beside the name the file gives it.

- **The box is the site itself**, drawn by the console: it follows the form *before* you save, so a
  palette, a mark, a section or a sentence can be judged without writing the file. A site added this
  session draws there too, although its own address only exists after the console's apply.
- **The fields are the keys below.** `enabled` and `slug` are structural: a save that changes either
  offers the apply that rebuilds the site table in place — no bot restart (see
  [Applying the change](#applying-the-change)).
- **The mark:** *Upload an image* stores one picture at four sizes and wins over everything;
  *Remove* falls back to `branding.logo`; with neither set the site draws the shipped product mark.
  See [Where a site's logo is read from](#where-a-sites-logo-is-read-from).
- **Expert mode**, at the foot of the page, is this file in an editor with a live check: a document
  that does not parse, a key this release does not know (named with its path), a site the loader
  would refuse, and an empty document are all refused, and nothing is written on a refusal.
- **Add a site**, beside it, appends one entry instead of replacing the file. Both blocks are the
  installation Admin's.

A managing scope may edit the content of its own site — the copy, the palette, the mark — while the
structure of the installation's sites (adding one, changing an address, the file itself) stays the
Admin's.

### The keys of a site block

| Key | Type | Default | Meaning |
|:----|:-----|:--------|:--------|
| *site name* | map key | — | The block's own name. `root` is reserved for the site without a group token. Any other name needs a `group_role`. |
| `slug` | string | the site's name | The URL segment the site is served under. The root site must leave it empty. Otherwise `[a-z0-9][a-z0-9-]*`, unique across sites, and not one of the shell's own paths (`login`, `logout`, `console`, `static`, `auth`). |
| `group_role` | string | none | The `managed_by` token this site shows, exactly as `servers.yaml` writes it. Required for every site except `root`, which takes none. |
| `enabled` | boolean | `true` | Whether the site is served at all. `false` keeps the block but registers no route, so the site owns no address — a switched-off root site does not move the console (see the warning under [Turning it on](#turning-it-on)). |
| `branding` | map | none | [Title, logo and footer](#branding). |
| `theme` | map | none | [Palette overrides](#theme). |
| `nav` | list of ids | `[overview, leaderboard, squadrons, players]` | The menu entries, in order. Known ids: `overview`, `leaderboard`, `squadrons`, `players`. A single string is refused (it would iterate as characters). |
| `tiles` | list of ids | `[active_week, online_now, playtime_total, avg_session, peak_hour]` | Known ids: `active_week`, `online_now`, `playtime_total`, `avg_session`, `peak_hour`. See the note below — this key is validated but nothing renders from it yet. |
| `lang` | string | `en` | The language the site renders in when a visitor has chosen none. Must be a language that has a `webui` catalog, otherwise the block is refused. |
| `character` | string | `pvpve` | The kind of community the site is: exactly `pvp`, `pve` or `pvpve`. It decides which figures a page **leads** with — never which figures exist. See [the community's character](#the-communitys-character). |

A typo is never silent: an unknown key, a non-list `nav` or `tiles`, an unknown id, an unknown
`character`, a duplicate or non-URL-safe slug, a `theme` block that names two palettes, or a
`group_role` no server names all raise at startup and stop the web UI from installing. The message
names the offending key.

!!! note "`tiles` changes nothing yet"
    `tiles:` is parsed, validated against the five ids and carried on the site, but no page reads it
    in this build: the overview's readout strip is a fixed set of six figures. Setting `tiles:` is
    accepted and has no visible effect. Removing a readout from the overview is not configurable.

### Which servers a site covers

Membership is not declared in the site block. `servers.yaml` is the only authority:

| Site | Shows |
|:-----|:------|
| a site with a `group_role` | every server whose `managed_by` names that token |
| the site named `root` | every server that declares no `managed_by` at all |
| a server naming two tokens | belongs to both of those sites, and to no other |

```yaml
# config/servers.yaml
Group-1:
  port: 10308
  managed_by:
    - 5th Guards
```

```yaml
# config/services/webservice.yaml
DEFAULT:
  frontend: true
  sites:
    root: {}                                      # servers with no managed_by
    guards:
      slug: guards
      group_role: "5th Guards"                    # served at /guards
```

Two consequences worth knowing before a report looks like a defect:

- On an installation where every server is tagged, `root` is empty. "Only one of my two servers
  shows" is usually the rule working.
- A `managed_by` written as a scalar (`managed_by: 5th Guards`) is read as a one-element list, with
  one warning naming the server. Write it as a list.

### A site with no servers

A site whose server set is empty is **not** refused. It is served normally: the membership is read
from the live server registry at request time, so a site whose servers are not running, or whose
token currently matches nothing, renders with an empty board — a `200` page whose figures are zero
and whose lists are empty.

The one case that is reported is a missing registry: a process that has no `ServiceBus` logs

```
Sites: this process has no ServiceBus (...). Every public site shows no servers until the
service bus is available in this process.
```

once per process. The site still answers — it just has no servers to show.

## The community's character

`sites.<name>.character` says what kind of community the site serves, and it is **declared, never
guessed**: no share of PvP kills can tell a quiet PvP server from a mixed one, so the site never
infers it from the data. Three values, nothing else — absent means `pvpve`:

| Value | The pilot page leads with | It still shows |
|:------|:--------------------------|:---------------|
| `pvp` | the player-versus-player record: PvP kills, the PvP K/D ratio, and the PvP figures first in the strip | the targets, the airframes and the competitive rating |
| `pve` | the targets (air, ground, air defence) and the air kills in the strip | the PvP record and the competitive rating |
| `pvpve` | both combat panels side by side, the strip in the balanced order | — |

It changes **emphasis, not content**: the same figures are always on the page, in the same sections.
The pilot page states the character in one line — *PvP community*, *PvE community*, *PvP and PvE
community* — so a visitor knows why the page leads with those numbers.

The leaderboard's default metric does **not** follow the character in this build: a visitor's own
choice always wins there, and the default stays the site's own.

```yaml
# config/services/webservice.yaml
DEFAULT:
  sites:
    root:
      character: pve        # a PvE community: targets and airframe hours lead
```

## Branding

Everything a site can wear lives in its own block. Every key is optional.

### `branding`

| Key | Type | Default | Where the value goes |
|:----|:-----|:--------|:---------------------|
| `title` | string | `DCSServerBot` | The site's name in the top bar and in the document `<title>`. |
| `logo` | string (path) | none — the shipped mark `/static/logo-mark.png` | The image in the top bar, instead of the shipped mark. Only raster images are served: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`. An `.svg` — or any other suffix — is not refused, but no logo route is registered and the shipped mark is drawn instead. |
| `footer_links` | list of `{label, href}` | `[]` — no footer links | The footer's anchors, in declaration order, right of the attribution. `href` must be an `http://`/`https://`/`mailto:` URL or a path beginning with `/`; no quotes, no `<`/`>` and no protocol-relative `//host` form. Any other shape is refused at startup. |

The footer's text is the product's attribution — `Powered by DCSServerBot` — written as a literal in
`services/webservice/templates/site_base.html`. **It is not configurable**: no key sets it, no site
can shorten it, and it is identical in every language. The only thing a site adds to the footer is
its own anchors (`footer_links`), which sit right of the attribution. There is no
`branding.footer_html` key and no site template is editable, so a site cannot replace the footer
either.

`branding.copyright` is gone from the editor and from the footer. A `site.yaml` (or a legacy
`config/services/webservice.yaml` block) that still carries it loads as before and the value is
ignored — the key is accepted so an older file never refuses to start, and it renders nowhere.

The one thing outside `site.yaml` that can reach the page's look is the operator's own uploaded
palette sheet (`theme.sheet`), which is served exactly as written — it is the operator's CSS, not a
site setting, and it can of course hide any element. Nothing in the site's configuration, and no
template, can remove the attribution.

`footer_links` entries are the whole vocabulary:

```yaml
    branding:
      footer_links:
        - {label: Imprint, href: /imprint}
        - {label: Discord, href: "https://discord.gg/example"}
```

A link whose `href` starts with `http` is rendered with `rel="noopener"`.

### `theme`

A site's palette has three sources, and a `theme` block names at most one of them.

| Key | Type | Default | Meaning |
|:----|:-----|:--------|:--------|
| `preset` | string | none — the shipped palette | The id of a shipped preset: `brass` (the shipped look), `slate`, `midnight` or `parchment`. A light preset sets the page's colour scheme with it. An id this build does not ship warns once and falls back to the shipped palette. |
| `sheet` | string | none — the shipped palette | The LEAF file name of a `.css` file in the site's asset folder (`site/` under the configuration directory), uploaded on the Public site page — which always stores it as `theme.css`. It is served **exactly as written**, with no validation; see [An uploaded sheet is served as written](#an-uploaded-sheet-is-served-as-written). A name whose file is not there is refused at startup, naming the key and the file. |
| `expert` | string | none — the shipped palette | The body of one `:root` rule: `--name: value;` declarations, one per line, at most 8 KB. The server wraps the text in `:root { … }`, so a selector cannot be expressed at all. |
| `light` | boolean | `false` | Sets `data-theme="light"` on the `<html>` element instead of `data-theme="dark"`. |

A block that sets two of `preset`, `sheet` and `expert` is refused at startup, naming both keys.

!!! note "`light` on its own changes nothing visible"
    The shipped site stylesheet declares no light palette — the console's light palette lives in
    `shell.css`, which a public page does not load. `light: true` sets the attribute; to get a light
    site, name a light preset (`slate` or `parchment`): a preset answers the page's colour scheme
    with its sheet, so the page follows what it renders.

#### A palette is token-only

A `preset` and the `expert` block re-declare the site's custom properties and nothing else. The
site's components are the shipped ones, and the shipped component selectors are an implementation
detail rather than a published interface: an `expert` block that targets a component is refused at
startup, because its grammar accepts declarations and nothing else, and the presets are files this
build itself ships.

#### An uploaded sheet is served as written

`theme.sheet` is the third source, and the only one that is **not** checked. The Public site page
(Public site → the palette block → *Your own sheet*) takes a `.css` file and stores it in the
site's asset folder as `theme.css`; `<site>/theme.css` then answers that file byte for byte.

What the page does check is the upload, not the CSS:

- the file must be named `*.css`;
- it must be UTF-8 text (a file that is not is refused rather than served with replacement
  characters);
- it must be at most 256 KB.

Everything else is the operator's. A sheet may therefore carry `@font-face`, `url()` and component
rules, and a browser loading it will fetch what it names **from this installation's own origin** —
so a sheet is code-adjacent content, exactly like a stylesheet you would add to the deployment
yourself. Upload only what you would put there. The expert sheet is the validated path; the upload
is for a sheet that already exists.

Re-uploading replaces the file (and the palette) at once; removing it clears `theme.sheet` and the
site falls back to its preset. Both are per-site subjects in the console's write gate
(`site.write`), and both carry the console's CSRF token.

The three sources stay exclusive in the save's direction too: picking a preset tile (or writing an
expert sheet) while an uploaded sheet is still stored is refused, naming both keys — remove the
sheet first, then choose.

A sheet does not decide the page's colour scheme: `theme.light: true` (or a light preset) does, since
`color-scheme` is not something a sheet's own values can set.

These are the custom properties the shipped stylesheet declares. All of them may be re-declared.

| Group | Custom properties |
|:------|:-----------------|
| Ground and ink | `--ground`, `--stage`, `--band`, `--panel`, `--panel-2`, `--line`, `--line-2`, `--black`, `--fg`, `--fg-2`, `--fg-3` |
| Accent | `--brass`, `--brass-dim`, `--brass-wash`, `--brass-hi`, `--sage` |
| Washes and glows | `--wash`, `--hair`, `--hero-glow`, `--hero-sage`, `--pulse-ring`, `--pulse-none`, `--pulse-edge` |
| Medals | `--medal`, `--medal-font`, `--medal-ink`, `--medal-gold`, `--medal-silver`, `--medal-bronze` |
| Type families | `--disp`, `--body`, `--mono` |
| Type scale | `--t-2xs`, `--t-xs`, `--t-sm`, `--t-md`, `--t-lg`, `--t-xl`, `--t-2xl`, `--t-3xl`, `--t-display`, `--t-giant`, `--t-phead`, `--t-display-sm`, `--t-giant-sm`, `--t-phead-sm`, `--t-chart` |
| Figure sizes | `--t-readout`, `--t-readout-sm`, `--t-panel-fig`, `--t-table-fig`, `--t-unit-giant`, `--t-unit`, `--t-unit-lg`, `--t-unit-xl` |
| Spacing | `--sp-0x`, `--sp-0y`, `--sp-0f`, `--sp-0h`, `--sp-0e`, `--sp-0t`, `--sp-0q`, `--sp-0s`, `--sp-0u`, `--sp-0v`, `--sp-0w`, `--sp-0z`, `--sp-1`, `--sp-2`, `--sp-3`, `--sp-4`, `--sp-5`, `--sp-6`, `--sp-7`, `--sp-8`, `--gap` |
| Page frame | `--max`, `--pad`, `--pad-y`, `--radius`, `--hair-w`, `--rule-w`, `--panel-pad` |
| Top bar and nav | `--top-h`, `--top-h-sm`, `--top-gap`, `--nav-gap`, `--mark-h`, `--mark-h-brand`, `--mark-gap`, `--search-w`, `--monogram` |
| Controls and marks | `--dot`, `--dot-offset`, `--bracket`, `--bar-h`, `--bar-h-lg`, `--focus-w`, `--focus-offset`, `--mock-h` |
| Hero and masthead geometry | `--hero-cap`, `--hero-pad-top`, `--hero-pad-bottom`, `--hero-foot-gap`, `--hero-cap-sm`, `--hero-cut-sm`, `--giant-pad-top`, `--giant-pad-bottom`, `--giant-gap`, `--giant-side`, `--phead-cap`, `--phead-cap-sm`, `--phead-cut-sm`, `--phead-offset`, `--phead-gap`, `--phead-pad-top`, `--phead-pad-bottom` |
| Chart, gauges and tables | `--tick`, `--tick-step`, `--tick-step-brass`, `--track-step`, `--hair-step`, `--p`, `--ring`, `--ring-inset`, `--ring-sm`, `--ring-sm-inset`, `--readout-h`, `--rank-w`, `--rank-w-sm` |

The eight values a rebrand actually needs are the ground and ink tokens plus the accent:
`--ground`, `--stage`, `--panel`, `--line`, `--fg`, `--fg-2`, `--fg-3` and `--brass`.

### Where a site's logo is read from

`branding.logo` is resolved like this:

- `~` and environment variables (`$HOME`, `%USERPROFILE%`) are expanded;
- an absolute path is used as it stands;
- a relative path is resolved against the bot's **configuration directory** — the `config/`
  directory holding `main.yaml` (the `--config` option, default `config`).

So `logo: site/crest.png` is `config/site/crest.png`. Write the path relative to `config/`, not to
the installation root: the schema's example spells `config/site/crest.png`, which would be read as
`config/config/site/crest.png`. An absolute path avoids the question entirely.

### A worked example

Two files: the site's block and its crest beside it.

```yaml
# config/services/webservice.yaml
DEFAULT:
  frontend: true
  sites:
    guards:
      slug: guards
      group_role: "5th Guards"
      branding:
        title: 5th Guards
        logo: site/crest.png
        footer_links:
          - {label: Imprint, href: /imprint}
          - {label: Discord, href: "https://discord.gg/example"}
      theme:
        expert: |
          --ground: #10141c;
          --stage: #0b0f15;
          --band: #1b2230;
          --panel: #161d29;
          --panel-2: #1d2534;
          --line: #2b3648;
          --line-2: #3d4a60;
          --fg: #eef2f8;
          --fg-2: #ccd4e0;
          --fg-3: #a3aebf;
          --brass: #6ea8ff;
          --brass-hi: #9cc4ff;
          --brass-dim: #3f6ea8;
          --brass-wash: rgba(110, 168, 255, .12);
      lang: en
      character: pvp
```

The `expert` value is the body of the sheet: the server wraps it in `:root { … }` and serves it at
`<site>/theme.css`. `theme: {preset: slate}` does the same job with a shipped sheet, in one line, and
`theme: {sheet: theme.css}` serves the `.css` file the Public site page uploaded.

```
config/
├── services/webservice.yaml
└── site/
    └── crest.png
```

What each piece changes on the page: the top bar and the browser tab read *5th Guards*, the top bar
draws `crest.png` in place of the shipped mark, every accent — section rules, the live dot, the
medals' surroundings, links — turns from brass to blue, and the footer reads
`Powered by DCSServerBot` followed by the two links.

### Applying the change

- The `sites:` block's STRUCTURE — which sites exist, their slugs, their switches — is applied when
  the console's apply runs, not when a browser merely reloads. A save on the Public site page
  offers that apply, and the apply rebuilds the site table in place, so a site switched off or on,
  or re-addressed, takes effect at once — no bot restart. A hand edit of `site.yaml` on disk takes
  effect when the apply is pressed, and not before: a browser reload alone re-reads nothing.
- The look's keys — `branding.*` and `theme.*` among them — were already resolved per request, so
  they never needed the apply and are unchanged by this. `theme.preset`, `theme.sheet` and
  `theme.expert` are resolved per request from the site file, and
  the sheet is answered with `Cache-Control: no-cache`, so a palette edit — or a re-uploaded sheet —
  shows on a normal reload. `branding.logo`
  is served with `Cache-Control: max-age=3600`, so a replaced crest can take up to an hour or a hard
  reload.

## Embedding it in an existing website

Four honest options, from the least to the most work.

### Link to it

The simplest thing that works: a menu entry or a link on the site you already run, pointing at
`https://<host>/` (the root site) or `https://<host>/<slug>`. Nothing else is needed — the public
site is anonymous, so a plain `<a href>` is enough.

### Put it in a frame

The service sends no frame-blocking header. Verified on a live response: an `X-Frame-Options`
header is absent, no `Content-Security-Policy` with `frame-ancestors` is sent, and the only headers
the page carries are `content-type` and `vary: Cookie`. So an `<iframe>` renders the site inside
your own page.

```html
<div style="position:relative;width:100%;aspect-ratio:16/10">
  <iframe src="https://stats.example.org/guards"
          title="5th Guards statistics"
          loading="lazy"
          style="position:absolute;inset:0;width:100%;height:100%;border:0"></iframe>
</div>
```

The wrapper is what makes it responsive: `aspect-ratio` gives the frame a height derived from its
width, and the absolutely positioned iframe fills it. `title` is the accessible name a screen
reader announces; `loading="lazy"` keeps the frame from fetching until it is scrolled near.

Two limits come with a frame, and neither is a bug:

- **The site's own top bar comes with it** — brand, menu, search, language pill and the sign-in
  chip, 72 px tall inside the frame. To embed just a table, use the API below instead.
- **A fixed-height frame cannot grow with the page.** The site is one scrolling document; an iframe
  is a window of a fixed size onto it, so the reader scrolls twice. Size the frame for the content
  you expect to be looked at, not for the whole page.

### Read the numbers from the API

The public site itself has no JSON twin for its pages. Exactly one JSON address exists on it —
`<site>/search.json` — and the overview, the leaderboard, the pilots page and the profile have no
JSON form at all. A site that wants the numbers in its own layout reads them from the REST API,
which is the surface that answers JSON:

```bash
curl -H "X-API-Key: <auth.api_key>" "http://localhost:9876/serverstats"
```

The REST API is mounted on the same WebService process and port, at the `prefix:` of the
`restapi` plugin's `DEFAULT` block — which **defaults to `""`**, so its paths live at the root.

| Endpoint | Method | Returns |
|:---------|:-------|:--------|
| `/serverstats` | GET | Statistics of a server, or of every server when `server_name` is omitted |
| `/server_attendance` | GET | Detailed attendance statistics, per server |
| `/leaderboard` | GET | The leaderboard for one metric (`what`), optional `server_name` |
| `/highscore` | GET | Highscore for a period (`period`), optional `server_name` |
| `/topkills` | GET | Top kills |
| `/topkdr` | GET | Top KDR |
| `/trueskill` | GET | TrueSkill ratings |
| `/stats` | POST | One player's statistics |
| `/modulestats` | POST | One player's module statistics |
| `/player_info` | POST | One player's information |
| `/weaponpk` | POST | Weapon PK for one player |
| `/traps` | POST | Carrier traps |
| `/traps/img` | GET | One trap's image |
| `/greenieboard` | POST | The greenieboard |
| `/events` | GET | Mission events |
| `/refuelings` | GET | Refuelling statistics |
| `/credits` | POST | Campaign credits |
| `/squadron_credits` | POST | Squadron campaign credits |
| `/squadrons` | GET | Every squadron and its roles |
| `/squadron_members` | POST | One squadron's members |
| `/player_squadrons` | POST | One player's squadrons |
| `/getuser` | POST | Users by name |
| `/current_server` | GET | The server a player is flying on |
| `/servers` | GET | Every server, its active mission and its extensions |

Every one of them is gated by the same rule, decided per request from the caller's address:

| Scope | Peer address | Default requirement |
|:------|:-------------|:--------------------|
| `local` | loopback | `none` — no credential |
| `trusted` | an address inside `security.trusted` (defaults: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) | `api_key` |
| `remote` | anything else | `jwt` |

- `api_key` is satisfied by an `X-API-Key` header carrying `auth.api_key`, or by a JWT.
- `jwt` is satisfied by `Authorization: Bearer <token>`, verified against `auth.jwt.jwks_url` with
  audience `dcssb` and a shared `key` claim.
- `security.default.<scope>` overrides the default for a scope; `endpoints.<path>.security.<scope>`
  overrides it for one endpoint, where `<path>` is the route without its leading slash — e.g.
  `endpoints.serverstats.security.remote: none`.
- `endpoints.<path>.enabled: false` does not register the route at all (it answers `404`).
- A word that is not one of `none`, `api_key`, `jwt` or `disabled` falls back to `jwt` with a
  warning, never to an open door.

Two things to keep in mind:

- **The API is installation-wide, not site-scoped.** A site's public pages show only its own
  servers; the API does not know about sites, so a caller sees every server the installation has.
  Filter by `server_name` where the endpoint accepts one, and keep `remote` at `jwt`.
- **Never put the API key in a page.** A browser-side `X-API-Key` is a published key. Proxy the
  call from your own backend, or publish only the public site.

### Carry the language

A visitor's stored language is the `dcssb_lang` cookie — one cookie, path `/`, valid for a year,
shared with the Command Center, so a choice made on one surface applies on the other.

A page of the public site renders in, in this order:

1. the `dcssb_lang` cookie, when it names a language that has a `webui` catalog;
2. `site.lang` — the language the site's block declares.

`Accept-Language` and `main.yaml` are not consulted on a public page: a site that declares German
stays German for a visitor who has chosen nothing.

A link may carry the choice. The switch is a plain GET to `<site>/language`, and it accepts `lang`
and `next`:

```html
<a href="https://stats.example.org/de/rankings">Rankings</a>
<a href="https://stats.example.org/de/language?lang=fr&next=/de/rankings">Français</a>
```

- `lang` must be one of the languages with a catalog: `en`, `cn`, `de`, `es`, `fr`, `it`, `pl`,
  `pt`, `ru`, `tr`. Anything else changes nothing and just returns the visitor where they were.
- `next` is validated against that site's own tree; anything else — an absolute URL, `//host`, a
  console path or another site's prefix — lands on the site's root instead.
- The address is `<site>/language`, so the root site's switch is the installation's fixed
  `/language`, which is the same route with the same cookie.

## Gotchas

**The pages are public, and the templates treat that as a rule.** Nothing that identifies a person
or an account reaches a public page: no Discord ID, no e-mail address, no IP address or hash, no
ban, watch or moderation state, no session detail. A pilot profile shows the pilot's figures, the
airframes and servers they flew, and their other UCIDs — never the Discord account behind them, and
never the wall-clock time of a session. That is why the `/me` page is a convenience over figures the
profile already publishes, not an extra disclosure.

**The figures are only as fresh as the last refresh.** The site reads materialised views
(`mv_serverstats`, `mv_site_activity`, `mv_site_pilots`, `mv_site_pilot_totals`,
`mv_site_pilot_aircraft`, `mv_site_missions`, `mv_site_aircraft`, `mv_site_hours`), all of them owned
and refreshed by the `userstats` plugin: its hourly task refreshes them **every hour**, concurrently,
`mv_statistics` first (`mv_serverstats` is built from it). A fresh install — and any install that has
just gained one of these views — shows an empty board, or zeros, until that refresh has run once. To
populate them at once:

```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_statistics;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_serverstats;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_site_activity;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_site_pilots;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_site_pilot_totals;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_site_pilot_aircraft;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_site_missions;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_site_aircraft;
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_site_hours;
```

`mv_site_hours` — the "When we fly" panel — is split across the hours a session actually spanned:
each session's `hop_on` … `hop_off` span is cut at the UTC hour boundaries and the flight seconds are
summed per hour, so a pilot who joins at 19:00 and flies until 23:00 fills four bars instead of one.
The panel draws 24 bars, one per UTC hour.

An empty board with a populated `statistics` table is that refresh, not a broken page.

**If a page answers 500** the reason is in the bot's own log, `logs/dcssb-<node>.log` — the site
runs inside the bot's process, so there is no second log file. A `404` on a site address is usually
a mistyped slug: a first path segment that names no configured site answers `404` and never falls
back to the root site.