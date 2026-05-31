# Juggling Tech Tree (Claude Generated)

An interactive "video-game tech tree" of juggling tricks, built from the
prerequisite links on [libraryofjuggling.com](https://libraryofjuggling.com).
Each trick is a node; an arrow **A → B** means "A is a prerequisite for B."
Tricks are laid out top-to-bottom by how deep they sit in the prerequisite
chain (foundations like the Cascade at the top), and colored by the site's
1–10 difficulty rating.

## View it

Just open **`index.html`** in a browser (double-click it — no server needed;
the graph data is inlined in `graphData.js`, and the graph library loads from a
CDN, so you need to be online the first time).

### What you can do
- **Click a trick** → highlights its full prerequisite chain (orange, upstream)
  and everything it unlocks (blue, downstream), and shows details in the side
  panel with a link to its libraryofjuggling page.
- **Double-click a trick** → opens its libraryofjuggling page in a new tab.
- **Hover** → quick tooltip (ball count, difficulty, tier).
- **Search** for a trick by name.
- **Filter** by ball count (3/4/5/6) with the chips.
- **Optional prerequisites** are drawn as dashed purple edges; toggle them off.
- Node **color** = difficulty (green → red). Node **shape/outline** = ball
  count. Roots (no required prereq) have a gold outline.

## Regenerate the data

```bash
python3 scrape.py
```

This re-reads the site and rewrites `graph.json` + `graphData.js`. It uses only
the Python standard library (no `pip install`). Pages are cached under
`cache/`, so re-runs are instant and don't hit the server again — delete
`cache/` to force a fresh crawl. The scraper is polite: a descriptive
User-Agent and a ~0.4s delay between live requests.

At the end it prints a summary (node/edge counts, roots, max depth) and any
warnings (unresolved links, cycles, missing difficulties). A clean run reports
no warnings.

## How it works

The key insight: every trick page lists its prerequisites as **hyperlinks** in a
fixed metadata block, e.g.

```html
<strong>Prerequisites:</strong> <a href="Box.html">Box</a>,
<a href="Shower-Cascade.html">Shower-Cascade</a> (optional)
```

So the dependency edges are read straight out of the HTML — no guessing.
`scrape.py`:

1. Seeds the node list from `TricksByDifficulty.html` (~174 tricks).
2. Fetches each trick page, parsing difficulty, prerequisite links (with an
   `(optional)` flag), and related tricks.
3. Resolves each prerequisite link to a canonical trick, following any
   prerequisites not in the index until the graph is closed (no dangling edges).
4. Computes each trick's **dependency depth** (longest required-prereq path from
   a root) for the tiered layout, guarding against cycles.
5. Writes `graph.json` (plain data) and `graphData.js` (`const GRAPH = …`, used
   by the page).

### Files
| File           | Purpose                                            |
|----------------|----------------------------------------------------|
| `scrape.py`    | Scraper / graph builder (stdlib only)              |
| `graphData.js` | Generated graph, inlined for the page              |
| `graph.json`   | Same graph as plain JSON (for reuse/debugging)     |
| `index.html`   | Self-contained interactive visualization           |
| `cache/`       | Cached raw HTML (one file per fetched page)         |

## Notes
- The source site is hand-authored, so the scraper tolerates and logs
  format deviations rather than crashing. One known source typo
  (`ReverseSynchFountain` linking its prerequisite back to itself) is corrected
  in a small documented fixup map in `scrape.py`.
- Data reflects libraryofjuggling.com as of the last scrape; all credit for the
  trick tutorials and the prerequisite relationships goes to that site.
