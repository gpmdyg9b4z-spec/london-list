#!/usr/bin/env python3
"""Build the private planner file for London List's "Where next?" sheet.

Reads, never writes, these sources:
  - Getty shoot folders on the T7 drive (folder names give date and place;
    file names are matched to a Lightroom catalogue copy for times of day)
  - Getty shoot notes and the sessions archive (older shoots, frame counts)
  - On Foot blog posts, from the GitHub copy of the blog (origin/main)
  - DeepMeta sales totals per asset, harvested into a private JSON file

Writes:
  - planner.json for the phone: shoots, blog posts and a 0-1 sales score per
    area. No money amounts and no customer names.

Nothing this script reads or writes belongs in the London List repository.
Run:  python3 tools/build_planner.py --out "<iCloud folder>/planner.json"
"""
import argparse, datetime as dt, glob, html, json, math, os, re, sqlite3, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser('~')
DEFAULTS = {
    'getty': os.path.join(HOME, 'Documents/Getty Project'),
    # Shoot folders: several places, separated by '|'. Finished shoots move off the T7.
    'shoots': '/Volumes/Samsung PSSD T7/Getty Submissions|/Volumes/Samsung Backup/Getty Submissions',
    'blog': os.path.join(HOME, 'Documents/Projects/on-foot-blog'),
    'sales': os.path.join(HOME, 'Documents/Projects/planner-data/deepmeta-sales.json'),
    'lr': os.path.join(HOME, 'Documents/Projects/planner-data/lr-capture-times.json'),
    'out': os.path.join(HOME, 'Library/Mobile Documents/com~apple~CloudDocs/London List/planner.json'),
}
BLOG_URL = 'https://gpmdyg9b4z-spec.github.io/on-foot-blog/'
MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


# ── Gazetteer ──────────────────────────────────────────────────────────────
class Gazetteer:
    """Finds London List areas (or places outside London) named in a piece of text."""

    def __init__(self, path):
        data = json.load(open(path, encoding='utf-8'))
        self.outside = data['outside']
        names = []   # (name, key, needs_london)
        for area, aliases in data['areas'].items():
            for a in aliases:
                names.append((self.clean(a.lstrip('!')).strip(), area, a.startswith('!')))
        for key, v in self.outside.items():
            for a in v['names']:
                names.append((self.clean(a.lstrip('!')).strip(), 'outside:' + key, a.startswith('!')))
        names.sort(key=lambda n: -len(n[0]))   # longest first: "cambridge square" before "cambridge"
        self.names = [(re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)"), key, need) for n, key, need in names]

    @staticmethod
    def clean(text):
        # Apostrophes and full stops are dropped on both sides: Getty titles often lose
        # them ("St. Jamess Park" and "St James's Park" then read the same).
        return ' ' + re.sub(r'\s+', ' ', re.sub(r"['’‘.]", '', text.lower())) + ' '

    def find(self, text, assume_london=False):
        """All keys named in the text, in order of first appearance."""
        t = self.clean(text)
        london = assume_london or 'london' in t
        hits = []
        for rx, key, need in self.names:
            if need and not london:
                continue
            for m in rx.finditer(t):
                hits.append((m.start(), key))
                t = t[:m.start()] + ' ' * (m.end() - m.start()) + t[m.end():]   # a span counts once
        seen, out = set(), []
        for _, key in sorted(hits):
            if key not in seen:
                seen.add(key); out.append(key)
        return out


# ── Getty shoots ───────────────────────────────────────────────────────────
FOLDER_RX = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+(?:\d{1,2}\s+)?(.+?)(?:\s+(?:[IVX]+\s+)?\d{1,2})?(?:\s+-\s+.*)?$')

def parse_folder(name):
    m = FOLDER_RX.match(name.strip())
    if not m:
        return None
    try:
        dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    place = re.sub(r'\s+(dump|batch|probe\S*|judged|folder|to strip)\b.*$', '', m.group(2))   # archive prose
    place = re.sub(r'\s+[A-Z]$', '', place).strip()    # "Sydenham B" -> "Sydenham"
    return m.group(1), place


def getty_shoots(args, gaz, lr_times):
    """One entry per shoot folder, grouped later by date and area."""
    folders = {}   # folder name -> path (or None when only the archive or Lightroom names it)
    for root in args.shoots.split('|'):
        if not os.path.isdir(root):
            print(f'  not found, skipped: {root}', file=sys.stderr)
            continue
        for name in sorted(os.listdir(root)):
            if os.path.isdir(os.path.join(root, name)) and parse_folder(name):
                folders.setdefault(name, os.path.join(root, name))
    for name in lr_times:   # shoots Lightroom knows about but no drive lists
        if parse_folder(name):
            folders.setdefault(name, None)

    # Older shoots named in the sessions archive (April to June 2026)
    arch = os.path.join(args.getty, 'getty-sessions-archive.md')
    if os.path.exists(arch):
        text = open(arch, encoding='utf-8', errors='replace').read()
        for m in re.finditer(r"(20\d\d-\d\d-\d\d) (?:\d{1,2} )?([A-Z][A-Za-z.&' -]{2,40}?)(?= batch| folder| \(|[,.;:]|\n| -)", text):
            name = m.group(0).strip()
            pf = parse_folder(name)
            if not pf:
                continue
            keys = tuple(gaz.find(pf[1]))
            if keys and not any(parse_folder(f)[0] == pf[0] and tuple(gaz.find(parse_folder(f)[1])) == keys
                                for f in folders):
                folders[name] = None

    # Frame counts and dateline areas from the shoot notes
    notes = {}
    for path in glob.glob(os.path.join(args.getty, '*-notes.md')):
        text = open(path, encoding='utf-8', errors='replace').read()
        fm = re.search(r'Folder: (?:/Volumes/[^/]+/Getty Submissions/)?(.+?)(?:\. Previews|\n|$)', text)
        if not fm:
            continue
        folder = fm.group(1).strip().rstrip('.')
        frames = re.search(r'Frames: (\d+)', text)
        dl = re.search(r'Datelines?:([^\n]*)', text)
        notes[folder] = {
            'frames': int(frames.group(1)) if frames else None,
            'extra': gaz.find(dl.group(1), assume_london=True) if dl else [],
        }

    shoots = []
    for name, path in folders.items():
        date, place = parse_folder(name)
        if re.search(r'\bwith\b', place, re.I):
            continue   # a personal outing ("... with a friend"), not a shoot
        # Folder names come from trips worldwide, so ambiguous names ("Temple") need "London".
        keys = gaz.find(place)
        n = notes.get(name, {})
        for k in n.get('extra', []):
            if k not in keys and not k.startswith('outside:'):
                keys.append(k)
        if not keys:
            continue   # abroad, or a place the gazetteer doesn't know yet
        lr = lr_times.get(name)
        frames = n.get('frames')
        if frames is None and path:
            frames = sum(1 for f in os.listdir(path) if f.lower().endswith('.jpg')) or None
        if frames is None and lr:
            frames = lr[2]
        shoots.append({'d': date, 'p': place, 'keys': keys, 'f': frames, 't': lr[:2] if lr else None})
    return shoots


# ── Lightroom capture times ────────────────────────────────────────────────
def load_lr_times(path):
    """{folder name: ['HH:MM' first, 'HH:MM' last, photo count]} made by --lr-catalog."""
    if not os.path.exists(path):
        return {}
    return json.load(open(path, encoding='utf-8'))


def extract_lr_times(catalog, out):
    """Read capture times from a COPY of the Lightroom catalogue (never the live one)."""
    db = sqlite3.connect('file:' + catalog + '?mode=ro', uri=True)
    # Lightroom folders carry the shoot folder's name ("2026-10-24 3 Holland Park").
    q = """select fo.pathFromRoot, min(i.captureTime), max(i.captureTime), count(*) from Adobe_images i
           join AgLibraryFile f on f.id_local = i.rootFile
           join AgLibraryFolder fo on fo.id_local = f.folder
           where i.captureTime is not null group by fo.pathFromRoot"""
    res = {}
    for path, t0, t1, n in db.execute(q):
        name = path.rstrip('/').split('/')[-1]
        if parse_folder(name) and len(t0) >= 16 and t0[:10] == t1[:10]:
            if name not in res or n > res[name][2]:
                res[name] = [t0[11:16], t1[11:16], n]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(res, open(out, 'w'), separators=(',', ':'))
    print(f'  {len(res)} capture times saved to {out}')


# ── Blog posts ─────────────────────────────────────────────────────────────
def blog_posts(args, gaz):
    try:
        subprocess.run(['git', '-C', args.blog, 'fetch', '--quiet', 'origin'], check=False, timeout=60)
        idx = subprocess.run(['git', '-C', args.blog, 'show', 'origin/main:index.html'],
                             capture_output=True, text=True, check=True).stdout
    except Exception as e:
        print('  blog not readable:', e, file=sys.stderr)
        return []
    posts = []
    for card in re.findall(r'<a class="post-card"(.*?)</a>', idx, re.S):
        href = re.search(r'href="([^"]+)"', card)
        day = re.search(r'date-day">(\d+)<', card)
        my = re.search(r'date-monthyear">([A-Za-z]{3})\w* (\d{4})<', card)
        title = re.search(r'post-title">(.*?)</div>', card, re.S)
        label = re.search(r'post-location-label">(.*?)</div>', card, re.S)
        search = re.search(r'data-search="([^"]*)"', card)
        if not (href and my):
            continue
        strip = lambda s: html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()
        text = ' '.join([strip(label.group(1)) if label else '', strip(title.group(1)) if title else '',
                         search.group(1) if search else ''])
        london = bool(label and 'London' in label.group(1))
        keys = gaz.find(text, assume_london=london)
        if not keys:
            continue
        date = f'{my.group(2)}-{MONTHS[my.group(1).lower()[:3]]:02d}-{int(day.group(1)) if day else 1:02d}'
        posts.append({'d': date, 'title': strip(title.group(1)) if title else '',
                      'url': BLOG_URL + href.group(1), 'keys': keys[:2]})
    return posts


# ── Sales ──────────────────────────────────────────────────────────────────
def sales_by_key(args, gaz):
    """{key: {'score': 0-1, 'top': [titles]}}; money stays on the Mac."""
    if not os.path.exists(args.sales):
        print('  no sales file: the planner will work without sales', file=sys.stderr)
        return {}
    data = json.load(open(args.sales, encoding='utf-8'))
    per = {}
    if 'byKey' in data:   # already totalled per place in the browser (see harvest notes in README)
        per = {k: {'r': v['r'], 'n': v['n'], 'top': [(0, t) for t in v['top']]} for k, v in data['byKey'].items()}
    for a in ([] if 'byKey' in data else data.values()):
        for k in gaz.find(a['t'])[:1]:     # credit the first place a title names
            p = per.setdefault(k, {'r': 0.0, 'n': 0, 'top': []})
            p['r'] += a['r']; p['n'] += a['n']; p['top'].append((a['r'], a['t']))
    if not per:
        return {}
    # Spread on a log scale between the weakest and strongest place, so one big
    # seller doesn't flatten the rest.
    logs = {k: math.log1p(p['r']) for k, p in per.items()}
    lo, hi = min(logs.values()), max(logs.values())
    out = {}
    for k, p in per.items():
        p['top'].sort(key=lambda x: -x[0])
        out[k] = {'score': round((logs[k] - lo) / (hi - lo), 3) if hi > lo else 1.0,
                  'top': list(dict.fromkeys(t for _, t in p['top']))[:3]}
    return out


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k, v in DEFAULTS.items():
        ap.add_argument('--' + k, default=v)
    ap.add_argument('--lr-catalog', help='a COPY of the Lightroom catalogue: extract capture times, then exit')
    args = ap.parse_args()

    if args.lr_catalog:
        extract_lr_times(args.lr_catalog, args.lr)
        return

    gaz = Gazetteer(os.path.join(HERE, 'places.json'))
    shoots = getty_shoots(args, gaz, load_lr_times(args.lr))
    posts = blog_posts(args, gaz)
    sales = sales_by_key(args, gaz)

    areas, outside = {}, {}
    def slot(key):
        if key.startswith('outside:'):
            name = key[8:]
            o = gaz.outside[name]
            return outside.setdefault(name, {'lat': o['lat'], 'lon': o['lon'], 'shoots': [], 'blog': []})
        return areas.setdefault(key, {'shoots': [], 'blog': []})

    merged = {}   # one entry per day and area: several folders from one walk become one shoot
    for s in shoots:
        for k in s['keys']:
            m = merged.setdefault((k, s['d']), {'d': s['d'], 'p': [], 'f': 0, 't': None})
            if s['p'] not in m['p']:
                m['p'].append(s['p'])
            m['f'] += s['f'] or 0
            if s['t']:
                m['t'] = [min(m['t'][0], s['t'][0]), max(m['t'][1], s['t'][1])] if m['t'] else s['t']
    for (k, _), m in merged.items():
        e = {'d': m['d'], 'p': ' · '.join(m['p'][:3])}
        if m['f']: e['f'] = m['f']
        if m['t']: e['t'] = m['t']
        slot(k)['shoots'].append(e)
    for p in posts:
        for k in p['keys']:
            slot(k)['blog'].append({'d': p['d'], 'title': p['title'], 'url': p['url']})
    for k, v in sales.items():
        e = slot(k)
        e['fruit'] = v['score']; e['sold'] = v['top']
    for e in list(areas.values()) + list(outside.values()):
        e['shoots'].sort(key=lambda s: s['d']); e['blog'].sort(key=lambda b: b['d'])

    out = {'app': 'london-list-planner', 'version': 1,
           'builtAt': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
           'areas': areas, 'outside': outside}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(out, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    timed = sum(1 for s in shoots if s['t'])
    print(f'{len(shoots)} shoots ({timed} with times), {len(posts)} blog posts, '
          f'sales for {len(sales)} places -> {args.out}')


if __name__ == '__main__':
    main()
