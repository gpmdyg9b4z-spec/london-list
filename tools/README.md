# Planner data for “Where next?”

“Where next?” (the ✦ Next button) suggests half-day outings. On its own it uses the
places still on your list. With planner data loaded it also weighs:

- **Getty shoots**: where and when you've photographed, and at what time of day
  (shoot folder names on the T7 and Samsung Backup drives, the Getty notes and
  sessions archive, times from the Lightroom catalogue)
- **On Foot posts**: walks you've already written up (the blog's GitHub copy)
- **Sales**: a 0–1 score for each area, from DeepMeta. Amounts and customer
  names never leave the Mac.

The planner file is private. It goes to iCloud Drive and from there into Safari
on the iPhone. It is never part of this repository or the website.

## Refresh it

1. **Lightroom times** (after new shoots). Copy the catalogue, never use the live
   one, then:
   ```bash
   python3 tools/build_planner.py --lr-catalog /path/to/copy/of/catalog.lrcat
   ```
   This writes `~/Documents/Projects/planner-data/lr-capture-times.json`.
2. **Sales** (now and then). Ask Claude to harvest DeepMeta sales: it reads the
   sales list in the signed-in browser pane, totals it per area there, and saves
   `~/Documents/Projects/planner-data/deepmeta-sales.json`.
3. **Build**:
   ```bash
   python3 tools/build_planner.py
   ```
   This writes `iCloud Drive › London List › planner.json`.
4. On the iPhone: London List → ⇅ Backup → **Load planner data** → choose that file.

## Places

`places.json` maps place names to London List areas, and lists places outside
London with a centre point. A name starting with `!` (such as `!victoria`) only
counts when the text also says London. Add names here when a shoot or post isn't
being picked up.
