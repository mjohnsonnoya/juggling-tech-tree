#!/usr/bin/env python3
"""
Scrape libraryofjuggling.com into a juggling-trick dependency graph.

Each trick page encodes its prerequisites as hyperlinks in a fixed metadata
block, so the dependency edges are read straight out of the HTML -- no NLP or
guessing. We seed from the site's "Tricks by Difficulty" index, fetch every
trick page (cached locally + rate-limited), parse difficulty / prerequisites /
related tricks, follow prerequisite links to closure, compute each trick's
dependency depth, and emit graph.json + graphData.js for the visualization.

Stdlib only -- no pip installs required.
"""

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://libraryofjuggling.com/"
INDEX_PATH = "TricksByDifficulty.html"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")
USER_AGENT = (
    "juggling-tech-tree/1.0 (personal research project; "
    "contact mjohnsonnoya@college.harvard.edu)"
)
REQUEST_DELAY = 0.4  # seconds between live (non-cached) requests

# A trick page lives at Tricks/{3,4,5,6}balltricks/Name.html
TRICK_HREF_RE = re.compile(r'Tricks/([3456])balltricks/[^"<>]+?\.html', re.I)

# Known typos in the source site's prerequisite hrefs: {page_path: {bad: good}}.
# ReverseSynchFountain links its "Synchronous Fountain" prereq back to itself.
HREF_FIXUPS = {
    "Tricks/4balltricks/ReverseSynchFountain.html": {
        "ReverseSynchFountain.html": "SynchFountain.html",
    },
}


# --------------------------------------------------------------------------- #
# Fetching (cached + polite)
# --------------------------------------------------------------------------- #
def cache_path_for(rel_path):
    """Map a site-relative path to a safe on-disk cache filename."""
    safe = rel_path.replace("/", "__")
    return os.path.join(CACHE_DIR, safe)


def fetch(rel_path):
    """Fetch a site-relative path, using the local cache when available.

    Returns the page text, or None on a 404 / fetch error.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cpath = cache_path_for(rel_path)
    if os.path.exists(cpath):
        with open(cpath, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    # URL-encode the path (handles parens, apostrophes, spaces) but keep "/".
    url = BASE + urllib.parse.quote(rel_path, safe="/")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - log and treat as missing
        print(f"  ! fetch failed for {rel_path}: {exc}", file=sys.stderr)
        return None
    finally:
        time.sleep(REQUEST_DELAY)

    with open(cpath, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
DIFFICULTY_RE = re.compile(r"Difficulty\s*\(1-10\):\s*</strong>\s*(\d+)", re.I)
TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
ANCHOR_RE = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)


def _clean_text(raw):
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


# Words that show up in a prereq listing but are not themselves prerequisites
# (separators / qualifiers like "Foo or Bar", "Baz (maybe)", "None").
_QUALIFIERS = {"none", "or", "and", "maybe", "both", "optional",
               "both optional", "either", "any"}
_SMALL_WORDS = {"in", "of", "the", "a", "an", "and", "or", "to"}


def _canonical_name(s):
    """Title-case a fundamental's name so case variants merge (two in one ->
    Two in One)."""
    words = s.split()
    return " ".join(
        w.lower() if (i > 0 and w.lower() in _SMALL_WORDS) else w.capitalize()
        for i, w in enumerate(words))


def _parse_fundamentals(leftover_html):
    """Pull plain-text (unlinked) prerequisites out of the prereq segment after
    its hyperlinks have been blanked out. These are 'fundamentals' -- precursor
    skills the site lists but gives no page (e.g. 'Two in One'). Returns a list
    of (canonical_name, optional) tuples."""
    text = _clean_text(leftover_html)
    found = []
    for chunk in re.split(r"\||,|\bor\b|\band\b", text, flags=re.I):
        optional = bool(re.search(r"optional|maybe", chunk, re.I))
        token = re.sub(r"\([^)]*\)", "", chunk).strip()   # drop "(optional)" etc.
        if len(token) < 3 or not re.search(r"[A-Za-z]", token):
            continue
        if token.lower() in _QUALIFIERS:
            continue
        found.append((_canonical_name(token), optional))
    return found


def fundamental_id(name):
    """Stable synthetic id for a pageless fundamental skill."""
    return "fund:" + re.sub(r"[^a-z0-9]", "", name.lower())


def _segment(page, label):
    """Return the HTML between `label:</strong>` and the next <br>, <strong>,
    or </li> -- i.e. just the value portion of a metadata line."""
    m = re.search(label + r"\s*:?\s*</strong>", page, re.I)
    if not m:
        return ""
    start = m.end()
    rest = page[start:]
    end = len(rest)
    for term in (r"<br", r"<strong", r"</li>", r"</ul>"):
        tm = re.search(term, rest, re.I)
        if tm:
            end = min(end, tm.start())
    return rest[:end]


def parse_trick(page):
    """Extract title, difficulty, prerequisites, related tricks from a page."""
    title_m = TITLE_RE.search(page)
    title = _clean_text(title_m.group(1)) if title_m else None

    diff_m = DIFFICULTY_RE.search(page)
    difficulty = int(diff_m.group(1)) if diff_m else None

    prereq_seg = _segment(page, "Prerequisites")
    prereqs = []
    # 1) Linked prerequisites (the normal case: a trick with its own page).
    for href, text in ANCHOR_RE.findall(prereq_seg):
        # Is this particular link annotated "(optional)" after the anchor?
        after = prereq_seg[prereq_seg.find('href="' + href + '"'):]
        # Look at the text immediately following this anchor's close tag.
        tail_m = re.search(re.escape(text) + r"</a>(.{0,30})", after, re.S)
        tail = tail_m.group(1).lower() if tail_m else ""
        optional = "(optional)" in tail or "optional" in tail[:15]
        prereqs.append({"kind": "link", "href": href,
                        "text": _clean_text(text), "optional": optional})
    # 2) Unlinked text prerequisites -> "fundamentals" (skills with no page).
    leftover = ANCHOR_RE.sub(" | ", prereq_seg)          # blank out the links
    for name, optional in _parse_fundamentals(leftover):
        prereqs.append({"kind": "fundamental", "name": name,
                        "optional": optional})

    related_seg = _segment(page, "Related Tricks")
    related = [{"href": href, "text": _clean_text(text)}
               for href, text in ANCHOR_RE.findall(related_seg)]

    return {
        "title": title,
        "difficulty": difficulty,
        "prereqs": prereqs,
        "related": related,
    }


# --------------------------------------------------------------------------- #
# Link / id resolution
# --------------------------------------------------------------------------- #
def resolve(href, from_path):
    """Resolve a (possibly relative) prereq href against the page's directory
    into a canonical site-relative trick path, or None if it's off-site / not
    a trick page."""
    href = href.strip()
    if href.startswith(("http://", "https://", "mailto:", "#")):
        # Absolute libraryofjuggling links are rare but handle them.
        if "libraryofjuggling.com" in href:
            href = href.split("libraryofjuggling.com/", 1)[1]
        else:
            return None
    base_dir = os.path.dirname(from_path)
    joined = os.path.normpath(os.path.join(base_dir, href)).replace("\\", "/")
    if not TRICK_HREF_RE.search(joined):
        return None
    return joined


def node_id(path):
    """Stable, readable node id from a trick path (drops folder + .html)."""
    return os.path.basename(path)[:-5] if path.endswith(".html") else path


def balls_from_path(path):
    m = re.search(r"Tricks/([3456])balltricks/", path)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# Crawl
# --------------------------------------------------------------------------- #
def seed_paths():
    """All trick paths linked from the difficulty index."""
    page = fetch(INDEX_PATH)
    if page is None:
        sys.exit("Could not fetch the index page; aborting.")
    seen, ordered = set(), []
    for m in TRICK_HREF_RE.finditer(page):
        p = m.group(0)
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def crawl():
    queue = seed_paths()
    print(f"Seed: {len(queue)} tricks from {INDEX_PATH}")
    nodes = {}          # path -> node dict
    fundamentals = {}   # fund_id -> synthetic node dict (pageless precursors)
    edges = []          # {source, target, optional}
    warnings = []
    queued = set(queue)

    while queue:
        path = queue.pop(0)
        page = fetch(path)
        if page is None:
            warnings.append(f"missing page: {path}")
            continue
        info = parse_trick(page)
        nid = node_id(path)
        name = info["title"] or nid
        nodes[path] = {
            "id": nid,
            "path": path,
            "name": name,
            "difficulty": info["difficulty"],
            "balls": balls_from_path(path),
            "url": BASE + urllib.parse.quote(path, safe="/"),
            "related": [r["text"] for r in info["related"]],
        }
        if info["difficulty"] is None:
            warnings.append(f"no difficulty parsed: {path}")

        for pre in info["prereqs"]:
            if pre["kind"] == "fundamental":  # pageless precursor skill
                fid = fundamental_id(pre["name"])
                if fid == nid:                # don't let a trick require itself
                    continue
                fundamentals.setdefault(fid, {
                    "id": fid, "path": None, "name": pre["name"],
                    "difficulty": None, "balls": None, "url": None,
                    "related": [], "fundamental": True})
                edges.append({"source": fid, "target": nid,
                              "optional": pre["optional"]})
                continue
            href = HREF_FIXUPS.get(path, {}).get(pre["href"], pre["href"])
            target = resolve(href, path)
            if target is None:
                warnings.append(
                    f"unresolved prereq '{pre['href']}' on {path}")
                continue
            if node_id(target) == nid:        # drop self-loops (data errors)
                warnings.append(f"self-loop prereq dropped on {path}")
                continue
            edges.append({"source": node_id(target), "target": nid,
                          "optional": pre["optional"]})
            if target not in queued:          # closure: pull in missing prereqs
                queued.add(target)
                queue.append(target)

    # Merge synthetic fundamental nodes in alongside the real trick pages.
    for fid, fnode in fundamentals.items():
        nodes[fid] = fnode

    return nodes, edges, warnings


# --------------------------------------------------------------------------- #
# Depth (longest required-prereq path from a root) + cycle guard
# --------------------------------------------------------------------------- #
def compute_depth(nodes, edges):
    ids = {n["id"] for n in nodes.values()}
    req_parents = {i: set() for i in ids}      # id -> required prereq ids
    for e in edges:
        if e["optional"]:
            continue
        if e["source"] in ids and e["target"] in ids:
            req_parents[e["target"]].add(e["source"])

    depth, visiting, done = {}, set(), set()
    cycle_warns = []

    def resolve_depth(i):
        if i in done:
            return depth[i]
        if i in visiting:                      # cycle: break defensively
            cycle_warns.append(i)
            return 0
        visiting.add(i)
        d = 0
        for p in req_parents[i]:
            d = max(d, resolve_depth(p) + 1)
        visiting.discard(i)
        done.add(i)
        depth[i] = d
        return d

    for i in ids:
        resolve_depth(i)
    return depth, cycle_warns


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    nodes, edges, warnings = crawl()
    depth, cycle_warns = compute_depth(nodes, edges)

    node_list = sorted(nodes.values(), key=lambda n: (n["balls"] or 9,
                                                       depth.get(n["id"], 0),
                                                       n["name"].lower()))
    for n in node_list:
        n["depth"] = depth.get(n["id"], 0)

    graph = {"nodes": node_list, "edges": edges}

    with open(os.path.join(HERE, "graph.json"), "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(HERE, "graphData.js"), "w", encoding="utf-8") as fh:
        fh.write("// Auto-generated by scrape.py -- do not edit by hand.\n")
        fh.write("const GRAPH = ")
        json.dump(graph, fh, ensure_ascii=False)
        fh.write(";\n")

    # ----- summary -----
    roots = [n["id"] for n in node_list if n["depth"] == 0]
    max_depth = max((n["depth"] for n in node_list), default=0)
    n_fundamental = sum(1 for n in node_list if n.get("fundamental"))
    by_balls = {}
    for n in node_list:
        by_balls[n["balls"]] = by_balls.get(n["balls"], 0) + 1

    print("\n=== Summary ===")
    print(f"nodes: {len(node_list)} ({n_fundamental} fundamental)   "
          f"edges: {len(edges)}   "
          f"optional edges: {sum(1 for e in edges if e['optional'])}")
    print(f"by ball count: " +
          ", ".join(f"{k}-ball: {v}" for k, v in sorted(by_balls.items(),
                    key=lambda x: (x[0] is None, x[0]))))
    print(f"roots (no required prereq): {len(roots)}   max depth: {max_depth}")
    if cycle_warns:
        print(f"\n! cycles broken at: {sorted(set(cycle_warns))}")
    if warnings:
        print(f"\n! {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  - {w}")
    print("\nWrote graph.json and graphData.js")


if __name__ == "__main__":
    main()
