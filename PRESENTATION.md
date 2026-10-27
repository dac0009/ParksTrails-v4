# Trail presentation site

A scroll-through explainer for Parks staff. It covers where the Mercer County Park trail data is today, what the pipeline does, what it suggests, and how one trail layer could run a public map, a staff map, dashboards and data requests. It is hosted on GitHub Pages from the `docs/` folder.

Live site: `https://dac0009.github.io/ParksTrails-v4/`
Editor:    `https://dac0009.github.io/ParksTrails-v4/?edit`

## Files

| Path | What it is |
|---|---|
| `docs/index.html` | The page. All default text lives here. |
| `docs/content.json` | Your text edits and hidden blocks. The page loads this on top of the defaults. The editor writes this file. |
| `docs/mapdata.js` | Map geometry traced from the five map PNGs. |
| `docs/.nojekyll` | Tells GitHub Pages to serve the files as-is. |
| `tools/map_trace/` | Scripts that rebuild `docs/mapdata.js` from the PNGs. |

## Turn on GitHub Pages (once)

1. Push or upload `docs/` and `tools/` to the `main` branch.
2. On GitHub, go to the repo **Settings → Pages**.
3. Under **Build and deployment**, set **Source: Deploy from a branch**, **Branch: main**, **Folder: /docs**, then **Save**.
4. After about a minute the site appears at the live URL above.

## Edit the text

1. Open the site with `?edit` at the end of the address.
2. Click any outlined text and type. Use **Shift+Enter** for a line break and **Ctrl/Cmd+B** for bold.
3. **Hide** removes a whole block, such as a step or a list item, from the live page. **Show** brings it back.
4. Changes are kept as a draft in your browser until you save.
5. Click **Save to GitHub**. The first time, it asks for a token:
   - GitHub → your photo → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   - **Repository access:** Only select repositories → `ParksTrails-v4`.
   - **Permissions → Repository permissions → Contents:** Read and write.
   - Paste the token into the editor. Tick "Remember" only on your own computer.
6. The editor commits `docs/content.json`. GitHub Pages republishes about a minute later.

If you don't want to use a token, click **Download content.json** and upload that file to `docs/` yourself.

To undo everything, set `docs/content.json` back to `{ "__hidden": [] }`.

## Update the maps

Export new PNGs from ArcGIS Pro at the same extent and with the same file names, put them in the repo root, then run:

```
pip install opencv-python-headless scikit-image shapely numpy
tools/map_trace/run.sh
```

The script prints a check of traced miles by status (official, unauthorized, other). The last run gave 17.4 / 7.3 / 1.0 mi against the records' 17.7 / 8.2 / 1.15.

Higher-resolution exports, such as 2× size, trace more cleanly. The page itself doesn't care about image size.

## What the map can and can't show

- The geometry is traced from map images, not read from the feature service. Lengths in the text come from the pipeline, not from the tracing.
- 8 of the 10 recommended loops are drawn (A–H). The other two share colors with these or sit under them in the export.
- The Red–Blue loop is split at its two side-trail junctions. The Blue side measures 1,327 ft (record: 1,328) and the Red side 2,686 ft (records: 1,930 + 776).
- Exporting the route layer and network edges from the notebook as GeoJSON would let the page draw every route exactly and show real distances.
