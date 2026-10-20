# =============================================================================
#  MERCER COUNTY PARK TRAILS - COMPLETE WORKFLOW (one notebook, no map needed)
#  ArcGIS Pro Notebook  |  Project: ParksTrails-v4
# -----------------------------------------------------------------------------
#  PIPELINE (run CELLS 0-12 in order):
#    AGOL segment layer (by URL) -> local snapshot -> Mercer County Park subset
#    -> EDITABLE working copy (yours; never overwritten unless you ask)
#    -> junction network -> park-wide routes -> scoring -> recommended set
#    -> review map + draft write-up
#  ON DEMAND:
#    CELL 13  Route Builder: select edges on the map -> validated custom route
#    CELL 14  Save names/decisions (survive reruns) -> MCP_Public_Routes
#  YOUR MANUAL FIXES: edit MCP_PhysicalTrails in Pro (snapping on), then rerun
#  from CELL 5. Edits are detected and listed in the write-up.
#  SAFETY: sources are read only; every write is confined to the work GDB.
# =============================================================================


# %% ==========================================================================
# CELL 0 - CONFIGURATION
# =============================================================================
import arcpy, os, re, csv, math, datetime, heapq, hashlib, time
from collections import defaultdict, Counter

C = {
    "NB_VERSION": "2026-10-20c",       # every cell checks this; paste CELL 0 from the SAME file version
    # --- sources (URL first; if it fails, the portal is searched by title) ---
    "TRAILS_URL": "https://utility.arcgis.com/usrsvcs/servers/b06af5c508a44ce7a42803653b675371/rest/services/Assets/MCPC_Park_Trails/FeatureServer/0",
    "TRAILS_SEARCH_TITLE": "Mercer County Park Commission Trails",
    "BOUNDARY_URL": None,
    "BOUNDARY_SEARCH_TITLE": "MCPC_Boundaries_Public",
    "PARK_NAME_EXACT_REGEX": r"^\s*mercer\s+county\s+park\s*$",
    "PARK_NAME_LOOSE": "mercer county park",
    "TRAIL_PARK_VALUES": ["MCP"],    # Park code on trail segments (Cell 4 showed Park = 'MCP' on all 227)
    "UNMARKED_MARKER_VALUES": ["grey", "gray", "none", ""],   # treated as NOT blazed (assumption, flagged)
    "EXPECTED_PARK_ACRES": (1500, 4000),

    # --- where outputs go. None = project home. OneDrive folders can lock or
    #     corrupt file geodatabases; a local folder like r"C:\GIS\MCP" is safer.
    "WORK_FOLDER": r"C:\GIS\MCP_ParksTrails",   # LOCAL, not OneDrive (sync + locks slow Pro down). None = project home.
    "WORK_GDB_NAME": "MCP_TrailsWork.gdb",
    "REPORT_FOLDER_NAME": "MCP_Reports",
    "REVIEW_MAP_NAME": "MCP Trail Routes",
    "WORK_SR_WKID": 103485,          # NAD83 CORS96 StatePlane NJ (US ft) - what your project used
    "FALLBACK_SR_WKID": 3424,        # NAD83 StatePlane NJ (US ft)

    # --- rerun behaviour --------------------------------------------------------
    "REFRESH_SOURCE_SNAPSHOT": True, # re-download the AGOL layer each run
    "REBUILD_WORKING_COPY": False,   # True ERASES your manual edits in MCP_PhysicalTrails

    # --- park selection / network repair (FEET) --------------------------------
    "SPATIAL_SEARCH_FT": 25.0,
    "NODE_SNAP_FT": 0.5,
    "AUTO_JOIN_FT": 5.0,
    "ASSUMED_JOIN_FT": 15.0,
    "CROSSINGS_AS_JUNCTIONS": True,
    "MIN_SELF_LOOP_FT": 2.0,

    # --- which trails may carry public routes -----------------------------------
    "INCLUDE_UNKNOWN_DESIGNATION": True,
    "EXPLORE_UNAUTHORIZED": True,

    # --- route generation ---------------------------------------------------------
    "LOOP_MIN_MI": 0.25, "LOOP_MAX_MI": 6.0,
    # Loop search: first a COMPLETE listing with small hard limits; if the network is too dense,
    # a bounded cycle-basis + beam search (flagged LOOPS_SAMPLED). Every limit below caps run time.
    "EXHAUSTIVE_MAX_LOOPS": 4000, "EXHAUSTIVE_WORK": 300000,                  # full network (incl. Unauthorized)
    "EXHAUSTIVE_MAX_LOOPS_PUBLIC": 20000, "EXHAUSTIVE_WORK_PUBLIC": 3000000,  # public network: try to PROVE completeness
    "SEED_CAP": 15000, "BEAM_PER_CLASS": 120, "BEAM_ROUNDS": 10, "LOOP_EVAL_CAP": 15000,
    "LENGTH_CLASSES": [("Short", 0.0, 1.0), ("Medium", 1.0, 3.0), ("Long", 3.0, 99.0)],
    "PICK_PER_CLASS": {"Short": 5, "Medium": 5, "Long": 4},
    "PICK_PARKS_DECISION": 6,
    "MAX_OVERLAP": 0.5,
    "MAX_CANDIDATES_WRITTEN": 500,    # candidates table keeps the top-scoring routes only (speed); recommended routes are always written
    "SPUR_MIN_MI": 0.15,
    "CONNECTOR_NAME_REGEX": r"connector|unnamed|^mcp_",
    "POC_LOOP_MI": 0.76,

    # --- CELL 13 Route Builder ----------------------------------------------------
    "BUILDER_ROUTE_NAME": "Custom route",
}


# %% ==========================================================================
# CELL 1 - SETUP, SAFETY GUARDS, HELPERS
# =============================================================================
def hdr(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)

NBV = "2026-10-20c"
if C.get("NB_VERSION") != NBV:
    raise RuntimeError("CELL 0 is from a different notebook version. Paste and run CELL 0 from this file, then CELL 1.")
aprx = arcpy.mp.ArcGISProject("CURRENT")
arcpy.env.addOutputsToMap = False        # tools must NOT drop layers into your maps (locks + slow redraws)
if C["WORK_FOLDER"]:
    WORK_FOLDER = C["WORK_FOLDER"]
elif "onedrive" in aprx.homeFolder.lower():
    WORK_FOLDER = os.path.join(os.path.expanduser("~"), "MCP_Trails_Work")   # local, not synced
else:
    WORK_FOLDER = aprx.homeFolder
try:
    os.makedirs(WORK_FOLDER, exist_ok=True)
except OSError:
    WORK_FOLDER = os.path.join(os.path.expanduser("~"), "MCP_ParksTrails")
    os.makedirs(WORK_FOLDER, exist_ok=True)
    print("Could not create C['WORK_FOLDER']; using", WORK_FOLDER)
_T = {"t": time.time()}
def lap(label):
    print("   (%s: %.1f s)" % (label, time.time() - _T["t"]))
    _T["t"] = time.time()
WORK_GDB = os.path.join(WORK_FOLDER, C["WORK_GDB_NAME"])
REPORT_DIR = os.path.join(WORK_FOLDER, C["REPORT_FOLDER_NAME"])
os.makedirs(REPORT_DIR, exist_ok=True)
if not arcpy.Exists(WORK_GDB):
    arcpy.management.CreateFileGDB(WORK_FOLDER, C["WORK_GDB_NAME"])
arcpy.env.overwriteOutput = True
RUN_TS = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
_ROOT = os.path.normcase(os.path.abspath(WORK_GDB))
print("Project :", aprx.filePath)
print("Work GDB:", WORK_GDB)
if "onedrive" in WORK_GDB.lower():
    print("WARNING: work GDB is inside OneDrive (lock/sync risk). Set C['WORK_FOLDER'] to a local folder.")

PROTECTED = {"mcp_physicaltrails", "mcp_route_decisions", "mcp_routes_custom"}
def assert_writable(path):
    p = str(path)
    if p.lower().startswith(("http://", "https://")):
        raise RuntimeError("SAFETY STOP: write to a web service: %s" % p)
    if not os.path.normcase(os.path.abspath(p)).startswith(_ROOT + os.sep):
        raise RuntimeError("SAFETY STOP: write outside work GDB: %s" % p)
    return path

def out_path(name, allow_protected=False):
    name = arcpy.ValidateTableName(name, WORK_GDB)
    if name.lower() in PROTECTED and not allow_protected:
        raise RuntimeError("SAFETY STOP: %s holds your edits/decisions and is not overwritten." % name)
    return assert_writable(os.path.join(WORK_GDB, name))

def gdbp(name):
    return os.path.join(WORK_GDB, name)

def count(p):
    return int(arcpy.management.GetCount(p)[0])

def is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")

def export_fc(src, out, where=None):
    assert_writable(out)
    arcpy.conversion.ExportFeatures(src, out, where_clause=where or "")

def coded_domains(fc):
    try:
        doms = {d.name: d.codedValues for d in arcpy.da.ListDomains(WORK_GDB) if d.domainType == "CodedValue"}
    except Exception:
        doms = {}
    return {f.name: doms[f.domain] for f in arcpy.ListFields(fc) if f.domain and f.domain in doms}

def user_fields(fc):
    return [f for f in arcpy.ListFields(fc) if f.type not in ("OID", "Geometry", "GlobalID", "Blob")
            and not f.name.upper().startswith(("SRC_", "MCP_", "SHAPE"))]

def detect_name_field(fc):
    best = None
    for f in user_fields(fc):
        n = f.name.lower()
        if f.type != "String" or any(x in n for x in ("park", "muni", "owner", "collector", "marker", "uid")):
            continue
        s = 3 if n in ("name", "trail_name", "trailname") else 2 if "name" in n else 0
        if s and (best is None or s > best[0]):
            best = (s, f.name)
    return best[1] if best else None

def write_csv(name, rows):
    p = os.path.join(REPORT_DIR, name)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    return p


# ---- stage guards: a cell refuses to run if an earlier cell failed or was skipped in this session ----
NEEDS = {2: [1], 3: [2], 4: [3], 5: [4], 6: [5], 7: [], 8: [6, 7], 9: [8], 10: [9], 11: [10], 12: [11], 13: [11], 14: [11]}
DONE = {1}
def begin(n, version):
    if version != NBV or C.get("NB_VERSION") != NBV:
        raise RuntimeError("CELL %d is from a different notebook version than CELLS 0-1. Paste all cells from one file." % n)
    missing = [k for k in NEEDS.get(n, []) if k not in DONE]
    if missing:
        raise RuntimeError("CELL %d needs CELL %s to finish first in this session (it failed or was not run)." % (n, missing))
    for k in [k for k in DONE if k >= n]:
        DONE.discard(k)            # rerunning a cell invalidates everything after it
def finish(n):
    DONE.add(n)
    lap("cell %d" % n)

def add_fields(fc, specs):
    """Add several fields in ONE geoprocessing call (AddField per field costs ~1-2 s each)."""
    have = {f.name.lower() for f in arcpy.ListFields(fc)}
    rows = [[n, t, "", ln if t == "TEXT" else ""] for n, t, ln in specs if n.lower() not in have]
    if not rows:
        return
    try:
        arcpy.management.AddFields(fc, rows)
    except Exception:
        for n, t, _, ln in rows:
            arcpy.management.AddField(fc, n, t, field_length=ln or None)
CHECKS, SHORTCUTS = [], []
def check(name, ok, detail=""):
    CHECKS.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
    print("   [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))

def shortcut(name, n, detail):
    SHORTCUTS[:] = [s for s in SHORTCUTS if s["shortcut"] != name]
    SHORTCUTS.append({"shortcut": name, "count": n, "detail": detail})

def detach_work_layers():
    for mp in aprx.listMaps():
        for lyr in mp.listLayers():
            try:
                if lyr.supports("DATASOURCE") and os.path.normcase(os.path.abspath(lyr.dataSource)).startswith(_ROOT + os.sep):
                    mp.removeLayer(lyr)
            except Exception:
                pass
        for t in mp.listTables():
            try:
                if os.path.normcase(os.path.abspath(t.dataSource)).startswith(_ROOT + os.sep):
                    mp.removeTable(t)
            except Exception:
                pass

shortcut("NO_ACCESS_DATA", 1, "Parking/restroom layers not used (other ownership); routes not scored on trailhead access.")
print("Helpers loaded:", RUN_TS)


# %% ==========================================================================
# CELL 2 - RESOLVE SOURCES (read only; no map layers needed)
# =============================================================================
begin(2, "2026-10-20c")
hdr("CELL 2 - RESOLVE SOURCES")
def resolve(url, title, hint_rx):
    if url:
        try:
            arcpy.Describe(url)
            return url, "config URL"
        except Exception as e:
            print("   URL not readable (%s) - trying portal search" % str(e).splitlines()[0])
    try:
        from arcgis.gis import GIS
        gis = GIS("pro")
        for it in gis.content.search(query='title:"%s"' % title, item_type="Feature Layer", max_items=20):
            lyrs = it.layers or []
            for fl in lyrs:
                if len(lyrs) == 1 or re.search(hint_rx, fl.properties.name, re.I):
                    return fl.url, "portal item '%s' (%s)" % (it.title, it.id)
    except Exception as e:
        print("   portal search failed:", str(e).splitlines()[0])
    return None, None

TRAILS_SRC, how_t = resolve(C["TRAILS_URL"], C["TRAILS_SEARCH_TITLE"], r"trail")
BOUND_SRC, how_b = resolve(C["BOUNDARY_URL"], C["BOUNDARY_SEARCH_TITLE"], r"bound")
if not TRAILS_SRC:
    raise RuntimeError("Trail segment layer not reachable. Sign in to the portal in Pro, or paste the URL into C['TRAILS_URL'].")
for label, src, how in (("TRAILS", TRAILS_SRC, how_t), ("BOUNDARY", BOUND_SRC, how_b)):
    if not src:
        print("%-8s: not found (park will be selected by attribute only - flagged)" % label)
        continue
    d = arcpy.Describe(src)
    print("%-8s: %s\n          via %s | %s | %s | %d features" % (label, src, how, d.shapeType,
          d.spatialReference.name, count(src)))
    print("          fields:", ", ".join(f.name for f in arcpy.ListFields(src)))
if not BOUND_SRC:
    shortcut("NO_BOUNDARY_CHECK", 1, "Park boundary unavailable; trails selected by Park attribute only.")
finish(2)


# %% ==========================================================================
# CELL 3 - LOCAL SNAPSHOTS (your own copy; all fields, GlobalIDs, source keys)
# =============================================================================
begin(3, "2026-10-20c")
hdr("CELL 3 - LOCAL SNAPSHOTS")
def fingerprint(g, prec):
    if g is None:
        return ("NULL",)
    a, b = g.firstPoint, g.lastPoint
    return (round(a.X, prec), round(a.Y, prec), round(b.X, prec), round(b.Y, prec), g.pointCount)

def snapshot(src, name):
    out = out_path(name)
    if arcpy.Exists(out) and not C["REFRESH_SOURCE_SNAPSHOT"]:
        print("   reusing", out)
        return out
    try:
        with arcpy.EnvManager(preserveGlobalIds=True):
            export_fc(src, out)
    except Exception as e:
        print("   GlobalID-preserving export failed (%s); retrying plain export" % str(e).splitlines()[0])
        try:
            export_fc(src, out)
        except Exception as e2:
            print("   arcpy export failed (%s); using ArcGIS API download" % str(e2).splitlines()[0])
            from arcgis.gis import GIS
            from arcgis.features import FeatureLayer
            FeatureLayer(src, GIS("pro")).query(where="1=1", out_fields="*").save(WORK_GDB, os.path.basename(out))
    # source keys: SRC_OID / SRC_GLOBALID let every local row trace back to Parks' row
    prec = 7 if arcpy.Describe(src).spatialReference.type == "Geographic" else 2
    sg = next((f.name for f in arcpy.ListFields(src) if f.type == "GlobalID"), None)
    og = next((f.name for f in arcpy.ListFields(out) if f.type == "GlobalID" or f.name.lower() == "globalid"), None)
    by_gid, by_fp = {}, defaultdict(list)
    with arcpy.da.SearchCursor(src, ["OID@", "SHAPE@"] + ([sg] if sg else [])) as cur:
        for row in cur:
            gid = str(row[2]).upper() if sg and row[2] else None
            if gid:
                by_gid[gid] = row[0]
            by_fp[fingerprint(row[1], prec)].append((row[0], gid))
    add_fields(out, [("SRC_OID", "LONG", None), ("SRC_GLOBALID", "TEXT", 38), ("SRC_MATCH", "TEXT", 12)])
    stats = Counter()
    with arcpy.da.UpdateCursor(out, ["SHAPE@", "SRC_OID", "SRC_GLOBALID", "SRC_MATCH"] + ([og] if og else [])) as cur:
        for row in cur:
            gid = str(row[4]).upper() if og and row[4] else None
            if gid in by_gid:
                row[1:4] = [by_gid[gid], gid, "GLOBALID"]
            else:
                hits = by_fp.get(fingerprint(row[0], prec), [])
                row[1:4] = ([hits[0][0], hits[0][1], "GEOMETRY"] if len(hits) == 1 else
                            [None, None, "AMBIGUOUS" if hits else "UNMATCHED"])
            stats[row[3]] += 1
            cur.updateRow(row)
    n_s, n_o = count(src), count(out)
    print("   %s: %d/%d rows | keys %s" % (out, n_s, n_o, dict(stats)))
    check("snapshot %s complete" % name, n_s == n_o, "%d vs %d" % (n_s, n_o))
    return out

detach_work_layers()
SRC_TRAILS = snapshot(TRAILS_SRC, "SRC_Trails")
SRC_BOUND = snapshot(BOUND_SRC, "SRC_Boundaries") if BOUND_SRC else None
finish(3)


# %% ==========================================================================
# CELL 4 - MERCER COUNTY PARK SUBSET + EDITABLE WORKING COPY
# =============================================================================
begin(4, "2026-10-20c")
hdr("CELL 4 - PARK SUBSET + WORKING COPY")
try:
    WORK_SR = arcpy.SpatialReference(C["WORK_SR_WKID"])
except Exception:
    WORK_SR = arcpy.SpatialReference(C["FALLBACK_SR_WKID"])
U = 0.3048 / WORK_SR.metersPerUnit
MI = 5280.0 * U
print("Work CRS:", WORK_SR.name)

def export_projected(src, out, where=None):
    d = arcpy.Describe(src)
    env = {"outputCoordinateSystem": WORK_SR}
    try:
        if d.spatialReference.GCS.factoryCode != WORK_SR.GCS.factoryCode:
            tr = arcpy.ListTransformations(d.spatialReference, WORK_SR, d.extent)
            if tr:
                env["geographicTransformations"] = tr[0]
                print("   transformation:", tr[0])
    except Exception:
        pass
    with arcpy.EnvManager(**env):
        export_fc(src, out, where)

def park_where(fc):
    doms = coded_domains(fc)
    hits = []
    for f in arcpy.ListFields(fc):
        if (f.type != "String" and f.name not in doms) or f.name.upper().startswith(("SRC_", "MCP_")):
            continue
        c = Counter()
        for (v,) in arcpy.da.SearchCursor(fc, [f.name]):
            lab = str(doms.get(f.name, {}).get(v, v)) if v is not None else ""
            if "mercer" in lab.lower():
                c[(v, lab)] += 1
        for (code, lab), n in c.items():
            hits.append((f.name, code, lab, n, bool(re.match(C["PARK_NAME_EXACT_REGEX"], lab, re.I))))
    for exact in (True, False):
        pool = [h for h in hits if h[4] == exact and (exact or C["PARK_NAME_LOOSE"] in h[2].lower())]
        if pool:
            fld = sorted({h[0] for h in pool}, key=lambda f: (0 if "park" in f.lower() else 1, len(f)))[0]
            codes = [h[1] for h in pool if h[0] == fld]
            fd = arcpy.AddFieldDelimiters(fc, fld)
            q = ",".join(("'%s'" % str(c).replace("'", "''")) if isinstance(c, str) else str(c) for c in codes)
            return fld, set(codes), "%s IN (%s)" % (fd, q), hits
    return None, set(), None, hits

BND, BND_DIS = None, None
if SRC_BOUND:
    bf, _, bwhere, bh = park_where(SRC_BOUND)
    if bwhere:
        MCP_BND = out_path("MCP_Boundary")
        export_projected(SRC_BOUND, MCP_BND, bwhere)
        dis = BND_DIS = out_path("MCP_Boundary_Dissolved")
        arcpy.management.Dissolve(MCP_BND, dis)
        BND = next(arcpy.da.SearchCursor(dis, ["SHAPE@"]))[0]
        ac = BND.getArea("GEODESIC", "ACRES")
        print("Boundary: %s -> %.0f acres" % (bwhere, ac))
        check("park boundary area plausible", C["EXPECTED_PARK_ACRES"][0] <= ac <= C["EXPECTED_PARK_ACRES"][1], "%.0f ac" % ac)
    else:
        print("Mercer County Park not found in boundary attributes:", [(h[0], h[2]) for h in bh][:10])

PRJ = out_path("PRJ_Trails")
export_projected(SRC_TRAILS, PRJ)
lap("4a project trails")
pfield = next((f.name for f in arcpy.ListFields(PRJ) if f.name.lower() == "park"), None)
if C.get("TRAIL_PARK_VALUES") and pfield:
    tf, tcodes = pfield, set(C["TRAIL_PARK_VALUES"])
    twhere = "%s IN (%s)" % (pfield, ",".join("'%s'" % v for v in sorted(tcodes)))
else:
    tf, tcodes, twhere, _th = park_where(PRJ)
print("Trail park filter:", twhere or "none found")
add_fields(PRJ, [("MCP_IN_ATTR", "SHORT", None), ("MCP_IN_SPATIAL", "SHORT", None), ("MCP_PCT_IN", "DOUBLE", None),
                 ("MCP_SEL", "TEXT", 16), ("MCP_GEOD_FT", "DOUBLE", None)])
arcpy.management.CalculateGeometryAttributes(PRJ, [["MCP_GEOD_FT", "LENGTH_GEODESIC"]], "FEET_US")
lap("4b fields + geodesic lengths")
in_sp, inside = set(), Counter()
if BND_DIS:
    # one spatial-index query + one clip, instead of comparing every trail to the detailed boundary polygon
    sel_lyr = arcpy.management.MakeFeatureLayer(PRJ, "mcp_prj_sel")[0]
    arcpy.management.SelectLayerByLocation(sel_lyr, "INTERSECT", BND_DIS, "%s Feet" % C["SPATIAL_SEARCH_FT"], "NEW_SELECTION")
    in_sp = {r[0] for r in arcpy.da.SearchCursor(sel_lyr, ["SRC_GLOBALID"]) if r[0]}
    clip = r"memory\mcp_clip"
    try:
        arcpy.analysis.PairwiseClip(sel_lyr, BND_DIS, clip)
    except Exception:
        arcpy.analysis.Clip(sel_lyr, BND_DIS, clip)
    for gid, ln_ in arcpy.da.SearchCursor(clip, ["SRC_GLOBALID", "SHAPE@LENGTH"]):
        if gid:
            inside[gid] += ln_
    for tmp in (sel_lyr, clip):
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass
lap("4c spatial selection (batch)")
tally, no_key = Counter(), 0
flds = ["SRC_GLOBALID", "SHAPE@LENGTH", "MCP_IN_ATTR", "MCP_IN_SPATIAL", "MCP_PCT_IN", "MCP_SEL"] + ([tf] if tf else [])
with arcpy.da.UpdateCursor(PRJ, flds) as cur:
    for row in cur:
        gid, L = row[0], row[1]
        no_key += 0 if gid else 1
        ia = 1 if tf and row[6] in tcodes else 0
        isp = 1 if gid in in_sp else 0
        row[2], row[3], row[4] = ia, isp, (min(1.0, inside.get(gid, 0.0) / L) if isp and L else 0.0)
        row[5] = "ATTR+SPATIAL" if ia and isp else "ATTR_ONLY" if ia else "SPATIAL_ONLY" if isp else None
        tally[row[5] or "outside"] += 1
        cur.updateRow(row)
print("Selection:", dict(tally))
check("every trail row has a source key (needed for spatial match)", no_key == 0, "%d without SRC_GLOBALID" % no_key)
if not tf:
    for pf_ in [f.name for f in arcpy.ListFields(PRJ) if f.type == "String" and "park" in f.name.lower()
                and not f.name.upper().startswith(("MCP_", "SRC_"))]:
        vals = Counter(v for v, sel in arcpy.da.SearchCursor(PRJ, [pf_, "MCP_SEL"]) if sel)
        print("   %s values on in-park segments: %s" % (pf_, dict(vals.most_common(6))))
    shortcut("PARK_ATTRIBUTE_UNMATCHED", 1, "No Park value matched; segments selected spatially only "
             "(boundary + %.0f ft). Set TRAIL_PARK_VALUES to the code printed in Cell 4." % C["SPATIAL_SEARCH_FT"])
if tally.get("ATTR_ONLY") or tally.get("SPATIAL_ONLY"):
    print("   ATTR_ONLY = says Mercer County Park but lies outside boundary; SPATIAL_ONLY = inside but different Park value.")

PRISTINE = out_path("MCP_Trails_Pristine")
export_fc(PRJ, PRISTINE, "MCP_SEL IS NOT NULL")
PHYS = out_path("MCP_PhysicalTrails", allow_protected=True)
if not arcpy.Exists(PHYS) or C["REBUILD_WORKING_COPY"]:
    export_fc(PRISTINE, PHYS)
    print("Working copy created:", PHYS, "(%d features) - edit this one in Pro" % count(PHYS))
else:
    print("Working copy KEPT (your edits preserved):", PHYS, "(%d features)" % count(PHYS))

# detect your manual edits vs. the pristine Parks geometry
pg = {r[0]: (r[1], r[2]) for r in arcpy.da.SearchCursor(PRISTINE, ["SRC_GLOBALID", "SHAPE@", "Name"]) if r[0]}
EDITS = Counter()
EDIT_ROWS = []
with arcpy.da.SearchCursor(PHYS, ["SRC_GLOBALID", "SHAPE@", "Name"]) as cur:
    seen = set()
    for gid, g, nm in cur:
        if not gid:
            EDITS["added by you"] += 1
            EDIT_ROWS.append({"type": "ADDED", "name": nm, "src_globalid": None})
            continue
        seen.add(gid)
        if gid not in pg:
            EDITS["not in current Parks data"] += 1
        else:
            if g is not None and pg[gid][0] is not None and not g.equals(pg[gid][0]):
                EDITS["geometry edited"] += 1
                EDIT_ROWS.append({"type": "GEOMETRY", "name": nm, "src_globalid": gid})
            if (nm or "") != (pg[gid][1] or ""):
                EDITS["renamed"] += 1
                EDIT_ROWS.append({"type": "RENAMED", "name": nm, "src_globalid": gid})
    EDITS["deleted by you"] = len(set(pg) - seen)
print("Manual edits in working copy:", dict(EDITS) or "none")
write_csv("manual_edits.csv", EDIT_ROWS)
if sum(EDITS.values()):
    shortcut("MANUAL_EDITS", sum(EDITS.values()), "Working copy differs from Parks data: %s (see manual_edits.csv)." % dict(EDITS))

# quick profile of the attributes that matter for the public map
doms = coded_domains(PHYS)
for fld in ("Designation", "Status", "Rank", "Trailbed", "Difficulty", "Accessibility", "MarkerColor", "Use_Class"):
    if arcpy.ListFields(PHYS, fld):
        c = Counter(str(doms.get(fld, {}).get(v, v)) for (v,) in arcpy.da.SearchCursor(PHYS, [fld]))
        print("   %-14s %s" % (fld, dict(c.most_common(8))))
if arcpy.ListFields(PHYS, "Length"):
    rat = [a / b for a, b in arcpy.da.SearchCursor(PHYS, ["Length", "MCP_GEOD_FT"]) if a and b]
    if rat:
        r = sorted(rat)[len(rat) // 2]
        unit = ("feet" if 0.95 <= r <= 1.05 else "miles" if 0.00018 <= r <= 0.0002 else "meters" if 0.29 <= r <= 0.32
                else "WEB MERCATOR METERS (about 31% longer than true length)" if 0.37 <= r <= 0.43 else "UNKNOWN")
        if unit.startswith("WEB"):
            shortcut("PARKS_LENGTH_FIELD_INFLATED", 1, "Parks' Length field is in Web Mercator meters (ratio %.3f to true feet), "
                     "about 31%% too long. Public mileage in this project uses true geodesic length; tell Parks before "
                     "anyone publishes the Length field." % r)
        print("   Length field looks like %s (median ratio %.5f to geodesic feet)" % (unit, r))
finish(4)


# %% ==========================================================================
# CELL 5 - READ WORKING COPY AND CLASSIFY  (rerun from here after manual edits)
# =============================================================================
begin(5, "2026-10-20c")
hdr("CELL 5 - READ AND CLASSIFY")
doms = coded_domains(PHYS)
def fld(rx):
    for f in user_fields(PHYS):
        if re.search(rx, f.name, re.I):
            return f.name
    return None
NAME_F, DESIG_F, STATUS_F = detect_name_field(PHYS), fld(r"^designation$"), fld(r"^status$")
SURF_F, DIFF_F, RANK_F, MARK_F = fld(r"trailbed|surface"), fld(r"difficult"), fld(r"^rank$"), fld(r"markercol")
ACC_F = fld(r"^accessib")
UNMARKED = {v.lower() for v in C.get("UNMARKED_MARKER_VALUES", [])}
def norm_marker(v):
    t = str(v).strip() if v is not None else ""
    return None if t.lower() in UNMARKED else t.title()
if UNMARKED:
    shortcut("MARKER_ASSUMPTION", 1, "MarkerColor values %s treated as NOT blazed (connectors); other colors count as "
             "blazed for wayfinding. Confirm with Parks." % sorted(UNMARKED - {""}))
print("name=%s designation=%s status=%s surface=%s difficulty=%s rank=%s marker=%s"
      % (NAME_F, DESIG_F, STATUS_F, SURF_F, DIFF_F, RANK_F, MARK_F))

def desig_class(d, s):
    ds, ss = str(d or "").lower(), str(s or "").lower()
    if "closed" in ss:
        return "CLOSED"
    if "proposed" in ds or "proposed" in ss:
        return "PROPOSED"
    if re.search(r"unauthori|unofficial|social|informal", ds):
        return "UNAUTHORIZED"
    return "OFFICIAL" if "official" in ds else "UNKNOWN"

def surface_class(v):
    t = str(v or "").lower()
    if not t.strip():
        return "Unknown"
    for rx, lab in ((r"pav|asphalt|concrete|blacktop", "Paved"), (r"gravel|stone|crush|dust", "Gravel"),
                    (r"board|bridge", "Boardwalk"), (r"natur|dirt|soil|earth|grass|mulch|wood|turf", "Natural")):
        if re.search(rx, t):
            return lab
    return str(v)

def diff_rank(v):
    t = str(v or "").lower()
    return 1 if re.search(r"easy|beginner", t) else 2 if re.search(r"moder|intermed", t) else \
        3 if re.search(r"difficult|hard|advanced|strenuous", t) else 0

def public_name(n):
    return "Connector" if re.search(C["CONNECTOR_NAME_REGEX"], str(n), re.I) else str(n)

TIER_OF = {"OFFICIAL": 0, "UNKNOWN": 1 if C["INCLUDE_UNKNOWN_DESIGNATION"] else 2, "UNAUTHORIZED": 2,
           "PROPOSED": 9, "CLOSED": 9}
MAX_TIER = 2 if C["EXPLORE_UNAUTHORIZED"] else 1

rf = ["OID@", "SHAPE@", "SRC_GLOBALID"] + [f for f in dict.fromkeys([NAME_F, DESIG_F, STATUS_F, SURF_F, DIFF_F, RANK_F, MARK_F, ACC_F]) if f]
feats, parts = [], []
with arcpy.da.SearchCursor(PHYS, rf) as cur:
    for row in cur:
        d = dict(zip(rf, row))
        dec = lambda f: (doms.get(f, {}).get(d[f], d[f]) if f else None)
        nm = str(dec(NAME_F)).strip() if NAME_F and not is_blank(d.get(NAME_F)) else "(unnamed)"
        cls = desig_class(dec(DESIG_F), dec(STATUS_F))
        fi = len(feats)
        feats.append({"oid": d["OID@"], "gid": d["SRC_GLOBALID"], "name": nm, "pub": public_name(nm), "cls": cls,
                      "tier": TIER_OF[cls], "surf": surface_class(dec(SURF_F)), "diff": dec(DIFF_F),
                      "rank": str(dec(RANK_F) or ""), "marker": norm_marker(dec(MARK_F)), "acc": dec(ACC_F)})
        g = d["SHAPE@"]
        if g is None:
            continue
        for pi in range(g.partCount):
            pts = [p for p in g.getPart(pi) if p]
            if len(pts) < 2:
                continue
            pl = arcpy.Polyline(arcpy.Array(pts), WORK_SR)
            if pl.length > 0:
                parts.append({"fi": fi, "pi": pi, "g": pl, "len": pl.length, "ext": pl.extent,
                              "a": (pts[0].X, pts[0].Y), "b": (pts[-1].X, pts[-1].Y)})
cls_mi = Counter()
name_mi = Counter()
for p in parts:
    cls_mi[feats[p["fi"]]["cls"]] += p["len"] / MI
    name_mi[feats[p["fi"]]["name"]] += p["len"] / MI
print("features %d | parts %d | miles by class %s" % (len(feats), len(parts), {k: round(v, 2) for k, v in cls_mi.items()}))
print("miles by name:", ", ".join("%s %.2f" % kv for kv in name_mi.most_common(20)))
finish(5)


# %% ==========================================================================
# CELL 6 - JUNCTION NETWORK (derived; repairs are explicit connector edges)
# =============================================================================
begin(6, "2026-10-20c")
hdr("CELL 6 - JUNCTION NETWORK")
SNAP, AUTO, ASSUME = C["NODE_SNAP_FT"] * U, C["AUTO_JOIN_FT"] * U, C["ASSUMED_JOIN_FT"] * U
EC = max(ASSUME * 20, 200 * U)

class UF:
    def __init__(self):
        self.p = []
    def add(self):
        self.p.append(len(self.p))
        return len(self.p) - 1
    def f(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x
    def u(self, a, b):
        ra, rb = self.f(a), self.f(b)
        if ra != rb:
            self.p[ra] = rb

def cell(x, y, c):
    return (int(math.floor(x / c)), int(math.floor(y / c)))

P, puf, marks = [], UF(), defaultdict(dict)
def mark(pid, m, x, y):
    for mm, i in marks[pid].items():
        if abs(mm - m) <= SNAP:
            return i
    P.append((x, y))
    i = puf.add()
    marks[pid][m] = i
    return i

end_pt, E = {}, []
for pid, p in enumerate(parts):
    end_pt[(pid, 0)] = mark(pid, 0.0, *p["a"])
    end_pt[(pid, 1)] = mark(pid, p["len"], *p["b"])
    E += [(p["a"][0], p["a"][1], pid, 0), (p["b"][0], p["b"][1], pid, 1)]
epgrid, pgrid = defaultdict(list), defaultdict(set)
for k, (x, y, pid, end) in enumerate(E):
    epgrid[cell(x, y, ASSUME)].append(k)
for pid, p in enumerate(parts):
    ex = p["ext"]
    for gx in range(int(math.floor((ex.XMin - ASSUME) / EC)), int(math.floor((ex.XMax + ASSUME) / EC)) + 1):
        for gy in range(int(math.floor((ex.YMin - ASSUME) / EC)), int(math.floor((ex.YMax + ASSUME) / EC)) + 1):
            pgrid[(gx, gy)].add(pid)

ep_pairs = []
for k, (x, y, pid, end) in enumerate(E):
    cx, cy = cell(x, y, ASSUME)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for j in epgrid.get((cx + dx, cy + dy), ()):
                if j <= k:
                    continue
                d = math.hypot(x - E[j][0], y - E[j][1])
                if d <= SNAP:
                    puf.u(end_pt[(pid, end)], end_pt[(E[j][2], E[j][3])])
                elif d <= ASSUME and E[j][2] != pid:
                    ep_pairs.append((k, j, d))

strict_mid, mid_cands = 0, []
for k, (x, y, pid, end) in enumerate(E):
    ptg = arcpy.PointGeometry(arcpy.Point(x, y), WORK_SR)
    for q in pgrid.get(cell(x, y, EC), ()):
        if q == pid:
            continue
        ex = parts[q]["ext"]
        if x < ex.XMin - ASSUME or x > ex.XMax + ASSUME or y < ex.YMin - ASSUME or y > ex.YMax + ASSUME:
            continue
        qp, along, dist, _ = parts[q]["g"].queryPointAndDistance(ptg)
        if dist > ASSUME or along <= SNAP or along >= parts[q]["len"] - SNAP:
            continue
        if dist <= SNAP:
            puf.u(end_pt[(pid, end)], mark(q, along, qp.firstPoint.X, qp.firstPoint.Y))
            strict_mid += 1
        else:
            mid_cands.append((k, q, along, dist, qp.firstPoint.X, qp.firstPoint.Y))

crossing_pts, n_cross = set(), 0
if C["CROSSINGS_AS_JUNCTIONS"]:
    pairs = set()
    for ids in pgrid.values():
        s_ = sorted(ids)
        pairs.update((s_[i], s_[j]) for i in range(len(s_)) for j in range(i + 1, len(s_)))
    for i, j in sorted(pairs):
        gi, gj = parts[i]["g"], parts[j]["g"]
        if gi.disjoint(gj):
            continue
        mp = gi.intersect(gj, 1)
        ends = (parts[i]["a"], parts[i]["b"], parts[j]["a"], parts[j]["b"])
        for idx in range(mp.pointCount):
            pt = mp.getPart(idx)
            if any(math.hypot(pt.X - e[0], pt.Y - e[1]) <= SNAP for e in ends):
                continue
            ptg = arcpy.PointGeometry(pt, WORK_SR)
            a_ = mark(i, gi.queryPointAndDistance(ptg)[1], pt.X, pt.Y)
            b_ = mark(j, gj.queryPointAndDistance(ptg)[1], pt.X, pt.Y)
            puf.u(a_, b_)
            crossing_pts.add(a_)
            n_cross += 1

root_size = Counter(puf.f(i) for i in range(len(P)))
dangling = lambda pt: root_size[puf.f(pt)] < 2
cands, joins, done = defaultdict(list), [], set()
for ka, kb, d in ep_pairs:
    pa, pb = end_pt[(E[ka][2], E[ka][3])], end_pt[(E[kb][2], E[kb][3])]
    if puf.f(pa) == puf.f(pb):
        continue
    da, db = dangling(pa), dangling(pb)
    if not da and not db and d <= AUTO:
        key = frozenset((puf.f(pa), puf.f(pb)))
        if key not in done:
            done.add(key)
            joins.append((pa, pb, d, "AUTO", "SPLIT_JUNCTION"))
    if da:
        cands[ka].append((d, "EP", kb))
    if db:
        cands[kb].append((d, "EP", ka))
for k, q, along, dist, qx, qy in mid_cands:
    if dangling(end_pt[(E[k][2], E[k][3])]):
        cands[k].append((dist, "MID", (q, along, qx, qy)))
for k in sorted(cands):
    d, typ, tgt = min(cands[k], key=lambda t: t[0])
    pa = end_pt[(E[k][2], E[k][3])]
    pb = end_pt[(E[tgt][2], E[tgt][3])] if typ == "EP" else mark(*tgt)
    key = frozenset((puf.f(pa), puf.f(pb)))
    if key in done:
        continue
    done.add(key)
    joins.append((pa, pb, d, "AUTO" if d <= AUTO else "ASSUMED", "ENDPOINT_GAP" if typ == "EP" else "MIDLINE_GAP"))

node_of_root, node_xy = {}, []
def node(pt):
    r = puf.f(pt)
    if r not in node_of_root:
        node_of_root[r] = len(node_xy)
        node_xy.append(P[r])
    return node_of_root[r]

edges, dropped_len, n_dropped = [], 0.0, 0
for pid, p in enumerate(parts):
    f = feats[p["fi"]]
    ms = sorted(marks[pid].items())
    for (m0, i0), (m1, i1) in zip(ms, ms[1:]):
        g = p["g"].segmentAlongLine(m0, m1)
        u_, v_ = node(i0), node(i1)
        if u_ == v_ and g.length < C["MIN_SELF_LOOP_FT"] * U:
            dropped_len += g.length
            n_dropped += 1
            continue
        edges.append({"kind": "TRAIL", "pid": "%s:%s" % (f["oid"], p["pi"]), "gid": f["gid"], "part": p["pi"],
                      "from_ft": m0 / U, "to_ft": m1 / U, "g": g, "len": g.length, "u": u_, "v": v_,
                      "name": f["name"], "pub": f["pub"], "cls": f["cls"], "tier": f["tier"], "surf": f["surf"],
                      "diff": f["diff"], "rank": f["rank"], "marker": f["marker"], "acc": f.get("acc"), "gap_ft": None})
n_auto = n_assumed = 0
for pa, pb, d, kind, why in joins:
    u_, v_ = node(pa), node(pb)
    if u_ == v_:
        continue
    g = arcpy.Polyline(arcpy.Array([arcpy.Point(*P[pa]), arcpy.Point(*P[pb])]), WORK_SR)
    edges.append({"kind": "GAP_" + kind, "pid": None, "gid": None, "part": None, "from_ft": None, "to_ft": None,
                  "g": g, "len": g.length, "u": u_, "v": v_, "name": "(gap join: %s)" % why, "pub": None,
                  "cls": "GAP", "tier": 0, "surf": None, "diff": None, "rank": "", "marker": None, "acc": None, "gap_ft": d / U})
    n_auto += kind == "AUTO"
    n_assumed += kind == "ASSUMED"
crossing_nodes = {node(p_) for p_ in crossing_pts}
shortcut("AUTO_GAP", n_auto, "Gaps <= %.0f ft joined with straight connector edges." % C["AUTO_JOIN_FT"])
shortcut("ASSUMED_GAP", n_assumed, "Dead ends %.0f-%.0f ft from a trail ASSUMED connected - verify." % (C["AUTO_JOIN_FT"], C["ASSUMED_JOIN_FT"]))
shortcut("CROSSING_AS_JUNCTION", n_cross, "Crossings without a shared vertex treated as at-grade junctions (could be bridges).")
print("edges %d (TRAIL %d, AUTO %d, ASSUMED %d) | nodes %d | crossings %d | T-junctions %d | slivers dropped %d"
      % (len(edges), sum(e["kind"] == "TRAIL" for e in edges), n_auto, n_assumed, len(node_xy), n_cross, strict_mid, n_dropped))
src_len = sum(p["len"] for p in parts)
check("length conserved", abs(sum(e["len"] for e in edges if e["kind"] == "TRAIL") + dropped_len - src_len) <= 0.001 * src_len)
check("edge measures valid", all(e["kind"] != "TRAIL" or e["from_ft"] < e["to_ft"] for e in edges))
finish(6)


# %% ==========================================================================
# CELL 7 - GRAPH ALGORITHMS (pure Python, tested)
# =============================================================================
begin(7, "2026-10-20c")
# >>> PURE-GRAPH
def build_adj(edges, max_tier):
    adj = defaultdict(list)
    for i, e in enumerate(edges):
        if e["tier"] <= max_tier:
            adj[e["u"]].append((i, e["v"]))
            adj[e["v"]].append((i, e["u"]))
    return adj

def build_links(edges, adj):
    anchors = {n for n, l in adj.items() if len(l) != 2}
    used, links = set(), []
    def walk(start, eid, nxt):
        steps = [(eid, edges[eid]["u"] == start)]
        used.add(eid)
        prev, cur = eid, nxt
        while cur not in anchors and cur != start:
            nx = [t for t in adj[cur] if t[0] != prev]
            if not nx or nx[0][0] in used:
                break
            ne, nn = nx[0]
            steps.append((ne, edges[ne]["u"] == cur))
            used.add(ne)
            prev, cur = ne, nn
        return {"a": start, "b": cur, "steps": steps, "len": sum(edges[s[0]]["len"] for s in steps)}
    for a in sorted(anchors):
        for eid, nb in adj[a]:
            if eid not in used:
                links.append(walk(a, eid, nb))
    for eid in sorted({i for l in adj.values() for i, _ in l}):
        if eid not in used:
            links.append(walk(edges[eid]["u"], eid, edges[eid]["v"]))
    return links

def link_adj(links):
    ladj = defaultdict(list)
    for lid, L in enumerate(links):
        ladj[L["a"]].append((lid, L["b"]))
        if L["a"] != L["b"]:
            ladj[L["b"]].append((lid, L["a"]))
    return ladj

def enum_cycles(links, max_len, cap, budget):
    """Every simple loop <= max_len (sets of links). Pruned by a shortest-path lower bound back to the
    start; `budget` caps search steps so it always ends quickly. Returns (cycles, truncated)."""
    ladj = link_adj(links)
    cycles, state = {}, {"trunc": False, "work": 0}
    for s in sorted(ladj):
        lb, pq = {s: 0.0}, [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > lb[u] or d > max_len:
                continue
            for lid, v in ladj[u]:
                nd = d + links[lid]["len"]
                if v >= s and nd < lb.get(v, float("inf")):
                    lb[v] = nd
                    heapq.heappush(pq, (nd, v))
        path, pset, vis = [], set(), {s}
        def dfs(u, L):
            state["work"] += 1
            if state["work"] > budget or state["trunc"]:
                state["trunc"] = True
                return
            for lid, v in ladj[u]:
                if lid in pset:
                    continue
                L2 = L + links[lid]["len"]
                if v == s:
                    if L2 <= max_len:
                        key = frozenset(pset | {lid})
                        if key not in cycles:
                            cycles[key] = path + [(lid, u, v)]
                            if len(cycles) >= cap:
                                state["trunc"] = True
                                return
                    continue
                if v < s or v in vis or L2 + lb.get(v, float("inf")) > max_len:
                    continue
                vis.add(v); pset.add(lid); path.append((lid, u, v))
                dfs(v, L2)
                path.pop(); pset.discard(lid); vis.discard(v)
        dfs(s, 0.0)
        if state["trunc"]:
            break
    return cycles, state["trunc"]

def expand_links(links, link_path):
    steps = []
    for lid, u, v in link_path:
        L = links[lid]
        steps += L["steps"] if (L["a"] == u and L["b"] == v) else [(e, not f) for e, f in reversed(L["steps"])]
    return steps

def step_nodes(edges, step):
    e, f = step
    return (edges[e]["u"], edges[e]["v"]) if f else (edges[e]["v"], edges[e]["u"])

def is_continuous(edges, steps, closed):
    for a, b in zip(steps, steps[1:]):
        if step_nodes(edges, a)[1] != step_nodes(edges, b)[0]:
            return False
    if closed and step_nodes(edges, steps[-1])[1] != step_nodes(edges, steps[0])[0]:
        return False
    return len({s[0] for s in steps}) == len(steps)

def order_subgraph(edges, eids):
    deg = Counter()
    for i in eids:
        deg[edges[i]["u"]] += 1
        deg[edges[i]["v"]] += 1
    ones = [n for n, d in deg.items() if d == 1]
    if all(d == 2 for d in deg.values()):
        shape, start = "LOOP", edges[eids[0]]["u"]
    elif len(ones) == 2 and all(d <= 2 for d in deg.values()):
        shape, start = "LINEAR", min(ones)
    else:
        return "SYSTEM", [(i, True) for i in eids]
    rem, cur, steps = set(eids), start, []
    while rem:
        nxt = next((i for i in sorted(rem) if cur in (edges[i]["u"], edges[i]["v"])), None)
        if nxt is None:
            return "SYSTEM", [(i, True) for i in eids]
        f = edges[nxt]["u"] == cur
        steps.append((nxt, f))
        rem.discard(nxt)
        cur = edges[nxt]["v"] if f else edges[nxt]["u"]
    return shape, steps
def bits(m):
    while m:
        low = m & -m
        yield low.bit_length() - 1
        m ^= low

def order_cycle(links, lids):
    """Order a link set as one simple cycle [(lid, from, to), ...]; None if it is not one."""
    inc = defaultdict(list)
    for lid in lids:
        inc[links[lid]["a"]].append(lid)
        inc[links[lid]["b"]].append(lid)
    if not lids or any(len(v) != 2 for v in inc.values()):
        return None
    first = min(lids)
    start = cur = links[first]["a"]
    lid, used, path = first, set(), []
    while True:
        L = links[lid]
        nxt = L["b"] if L["a"] == cur else L["a"]
        path.append((lid, cur, nxt))
        used.add(lid)
        cur = nxt
        if cur == start:
            break
        cand = [x for x in inc[cur] if x not in used]
        if not cand:
            return None
        lid = cand[0]
    return path if len(used) == len(lids) else None

def horton_seeds(links, ladj, max_len, cap):
    """Horton candidate cycles (contain a minimum cycle basis): {bitmask: length}."""
    seeds = {}
    for v in sorted(ladj):
        dist, prev, branch, pq = {v: 0.0}, {}, {v: None}, [(0.0, v)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for lid, w in ladj[u]:
                nd = d + links[lid]["len"]
                if nd <= max_len and nd < dist.get(w, float("inf")):
                    dist[w], prev[w] = nd, (u, lid)
                    branch[w] = lid if u == v else branch[u]
                    heapq.heappush(pq, (nd, w))
        for lid, L in enumerate(links):
            x, y = L["a"], L["b"]
            if x not in dist or y not in dist:
                continue
            tot = dist[x] + dist[y] + L["len"]
            if tot > max_len:
                continue
            if x == y:
                if x == v:
                    seeds.setdefault(1 << lid, L["len"])
                continue
            if prev.get(x, (None, None))[1] == lid or prev.get(y, (None, None))[1] == lid:
                continue
            if x != v and y != v and branch[x] == branch[y]:
                continue
            m = 1 << lid
            for n in (x, y):
                while n != v:
                    u, l2 = prev[n]
                    m |= 1 << l2
                    n = u
            seeds.setdefault(m, tot)
        if len(seeds) >= cap:
            break
    return seeds

def cycle_basis(seeds):
    """Shortest-first GF(2) elimination -> a minimum cycle basis (list of bitmasks)."""
    piv, basis = {}, []
    for m in sorted(seeds, key=lambda k: (seeds[k], k)):
        x = m
        while x:
            h = x.bit_length() - 1
            if h in piv:
                x ^= piv[h]
            else:
                piv[h] = x
                basis.append(m)
                break
    return basis

def grow_loops(links, max_len, evaluate, seed_cap, beam, rounds, eval_cap, classes):
    """Bounded loop search. Seeds = Horton cycles (contain a minimum cycle basis). Each round grows the
    best loops per length class by XOR with adjacent basis cycles AND with other good loops sharing a side
    (sizes can roughly double per round). Evaluation is split evenly across rounds and length classes,
    so long loops are reached instead of the budget being spent on short ones."""
    ladj = link_adj(links)
    seeds = horton_seeds(links, ladj, max_len, seed_cap)
    basis = cycle_basis(seeds)
    llen = [L["len"] for L in links]
    mlen = lambda m: sum(llen[l] for l in bits(m))
    cls_of = lambda L: next((c for c, lo, hi in classes if lo <= L < hi), classes[-1][0])
    per_round = max(50, eval_cap // (rounds + 1))
    results, n_eval = {}, [0]
    def ev(mask):
        if mask in results:
            return results[mask]
        if n_eval[0] >= eval_cap:
            return None
        lp = order_cycle(links, list(bits(mask)))
        results[mask] = evaluate(lp) if lp else None
        n_eval[0] += 1 if lp else 0
        return results[mask]
    def sample(cands):
        buckets = defaultdict(list)
        for m, L in cands.items():
            buckets[cls_of(L)].append((L, m))
        quota = max(1, per_round // max(1, len(buckets)))
        out = []
        for b in buckets.values():
            b.sort()
            step = max(1, len(b) // quota)
            out += [m for _, m in b[::step][:quota]]
        return out
    def best(masks):
        # per length class: ~70% of the beam for public-eligible loops (tier <= 1), the rest for loops that
        # need Unauthorized paths (PARKS_DECISION), so the search grows the loops that can actually be published
        g = defaultdict(lambda: ([], []))
        for m in masks:
            r = results.get(m)
            if r:
                g[r["class"]][0 if r["tier"] <= 1 else 1].append((-r["score"], m))
        keep_pub = int(math.ceil(beam * 0.7))
        out = []
        for pub, other in g.values():
            pub, other = sorted(pub), sorted(other)
            take = pub[:keep_pub]
            out += [m for _, m in take + other[:beam - len(take)]]
        return out
    frontier = best([m for m in sample(seeds) if ev(m)])
    by_lid = defaultdict(list)
    for b in basis:
        for lid in bits(b):
            by_lid[lid].append(b)
    done_rounds = 0
    for rd in range(rounds):
        if n_eval[0] >= eval_cap or not frontier:
            break
        fidx = defaultdict(list)
        for m in frontier:
            for lid in bits(m):
                fidx[lid].append(m)
        new = {}
        for m in frontier:
            partners = set()
            for lid in bits(m):
                partners.update(by_lid[lid])
                partners.update(fidx[lid])
            for b in partners:
                x = m ^ b
                if x and x not in results and x not in new:
                    L = mlen(x)
                    if L <= max_len:
                        new[x] = L
        if not new:
            break
        frontier = best([x for x in sample(new) if ev(x)])
        done_rounds = rd + 1
    stats = {"seeds": len(seeds), "basis": len(basis), "rounds": done_rounds, "evaluated": n_eval[0],
             "capped": n_eval[0] >= eval_cap}
    return [r for r in results.values() if r], stats
# <<< PURE-GRAPH
finish(7)


# %% ==========================================================================
# CELL 8 - ROUTE METRICS AND SCORING
# =============================================================================
begin(8, "2026-10-20c")
def setup_graph():
    global ADJ, DEG
    ADJ = build_adj(edges, MAX_TIER)
    DEG = {n: len(l) for n, l in ADJ.items()}
setup_graph()

def route_key(steps):
    toks = sorted("%s|%d|%d" % (edges[e]["gid"], round(edges[e]["from_ft"]), round(edges[e]["to_ft"]))
                  for e, _ in steps if edges[e]["kind"] == "TRAIL")
    return hashlib.md5(";".join(toks).encode()).hexdigest()[:16]

def route_metrics(steps, closed, rtype, label_hint=""):
    es = [edges[e] for e, _ in steps]
    L = sum(e["len"] for e in es) or 1e-9
    tr = [e for e in es if e["kind"] == "TRAIL"]
    name_len, surf = Counter(), Counter()
    for e in tr:
        name_len[e["pub"]] += e["len"]
        surf[e["surf"]] += e["len"]
    seq = [e["pub"] for e in tr]
    changes = sum(a != b for a, b in zip(seq, seq[1:])) + (1 if closed and len(seq) > 1 and seq[0] != seq[-1] else 0)
    nodes = {n for s in steps for n in step_nodes(edges, s)}
    decisions = sum(1 for n in nodes if DEG.get(n, 0) >= 3)
    pairs = list(zip(steps, steps[1:])) + ([(steps[-1], steps[0])] if closed and len(steps) > 1 else [])
    cross = sum(1 for a, b in pairs if step_nodes(edges, a)[1] in crossing_nodes
                and edges[a[0]]["kind"] == edges[b[0]]["kind"] == "TRAIL" and edges[a[0]]["pid"] != edges[b[0]]["pid"])
    tl = sum(surf.values()) or 1
    unk = sum(e["len"] for e in tr if e["cls"] == "UNKNOWN")
    una = sum(e["len"] for e in tr if e["cls"] == "UNAUTHORIZED")
    marked = sum(e["len"] for e in tr if not is_blank(e["marker"]))
    accessible = sum(e["len"] for e in tr if str(e.get("acc") or "").strip().lower() == "yes")
    primary = sum(e["len"] for e in tr if "primary" in e["rank"].lower())
    auto = sum(e["kind"] == "GAP_AUTO" for e in es)
    assumed = sum(e["kind"] == "GAP_ASSUMED" for e in es)
    dr = [(diff_rank(e["diff"]), str(e["diff"])) for e in tr if not is_blank(e["diff"])]
    difficulty = max(dr)[1] if dr else "Not recorded"
    flags = [x for x in ("AUTO_GAP x%d" % auto if auto else "", "ASSUMED_GAP x%d" % assumed if assumed else "",
                         "CROSSING_TURN x%d" % cross if cross else "",
                         "UNKNOWN_DESIG %.0f%%" % (100 * unk / L) if unk else "",
                         "UNAUTHORIZED %.0f%%" % (100 * una / L) if una else "") if x]
    score = (100 - 6 * changes - 2 * decisions - 10 * assumed - auto - 8 * cross - 25 * unk / L
             + 10 * marked / L + 5 * primary / L + (5 if surf and max(surf.values()) / tl >= 0.9 else 0))
    mi = L / MI
    cls = next((c for c, lo, hi in C["LENGTH_CLASSES"] if lo <= mi < hi), "Long")
    major = [n for n, l in name_len.most_common() if n != "Connector" and l / L >= 0.2]
    word = {"LOOP": "Loop", "SPUR": "Out-and-Back"}.get(rtype, "")
    label = label_hint or ("%s %s" % ("-".join(n.replace(" Trail", "") for n in major[:3]) or "Connector", word)).strip()
    return {"steps": steps, "closed": closed, "type": rtype, "len": L, "mi": round(mi, 2),
            "trip_mi": round(mi * (2 if rtype == "SPUR" else 1), 2), "class": cls,
            "tier": max(e["tier"] for e in es), "score": round(score, 1), "label": label,
            "names": ", ".join("%s %.0f%%" % (n, 100 * l / L) for n, l in name_len.most_common()),
            "changes": changes, "decisions": decisions,
            "surfaces": ", ".join("%s %.0f%%" % (s, 100 * l / tl) for s, l in surf.most_common()),
            "paved": round(100 * surf.get("Paved", 0) / tl, 1), "difficulty": difficulty,
            "marked_pct": round(100 * marked / L, 0), "access_pct": round(100 * accessible / L, 0), "flags": "; ".join(flags),
            "unauth_ft": round(una / U), "unknown_ft": round(unk / U), "eids": {e for e, _ in steps}}

def rkey(r):
    if "key" not in r:
        r["key"] = route_key(r["steps"])
    return r["key"]

def overlap(a, b):
    return sum(edges[e]["len"] for e in a["eids"] & b["eids"]) / min(a["len"], b["len"])
print("Metrics ready.")
finish(8)


# %% ==========================================================================
# CELL 9 - GENERATE PARK-WIDE CANDIDATES
# =============================================================================
begin(9, "2026-10-20c")
hdr("CELL 9 - GENERATE CANDIDATES")
ROUTES, FAILS = [], []
def add_route(r):
    if r["type"] != "NAMED_SYSTEM" and not is_continuous(edges, r["steps"], r["closed"]):
        FAILS.append(r["label"])
    else:
        ROUTES.append(r)

LINKS = build_links(edges, ADJ)
check("links cover routable edges once", sorted(e for L in LINKS for e, _ in L["steps"]) == sorted({i for l in ADJ.values() for i, _ in l}))
max_mi = C["LOOP_MAX_MI"]
MAXL = max_mi * MI
CLASSES_U = [(c, lo * MI, hi * MI) for c, lo, hi in C["LENGTH_CLASSES"]]

def find_loops(adj, label, max_loops, work):
    """Complete listing if it finishes within the small hard limits, else bounded basis + beam search."""
    lk = build_links(edges, adj)
    t0 = time.time()
    cyc, trunc = enum_cycles(lk, MAXL, max_loops, work)
    if not trunc:
        rs = [route_metrics(expand_links(lk, lp), True, "LOOP") for lp in cyc.values()]
        st = {"method": "complete", "loops": len(cyc)}
    else:
        rs, st = grow_loops(lk, MAXL, lambda lp: route_metrics(expand_links(lk, lp), True, "LOOP"),
                            C["SEED_CAP"], C["BEAM_PER_CLASS"], C["BEAM_ROUNDS"], C["LOOP_EVAL_CAP"], CLASSES_U)
        st["method"] = "basis+beam"
        shortcut("LOOPS_SAMPLED_" + label, len(rs), "%s network too dense to list every loop quickly; loops built from a "
                 "minimum cycle basis (%d) grown by beam search (%d rounds, %d evaluated%s)."
                 % (label, st["basis"], st["rounds"], st["evaluated"], ", capped" if st["capped"] else ""))
    print("   %-7s loops: %s, %d found, %.1f s" % (label, st["method"], len(rs), time.time() - t0))
    return rs, st

# Pass A: public-eligible network only -> every loop is publishable by construction
loops_pub, GSTATS_PUB = find_loops(build_adj(edges, 1), "PUBLIC", C["EXHAUSTIVE_MAX_LOOPS_PUBLIC"], C["EXHAUSTIVE_WORK_PUBLIC"])
loop_routes = [r for r in loops_pub if r["tier"] <= 1]
GSTATS = {"public": GSTATS_PUB}
# Pass B: full network, keep only loops that need Unauthorized paths -> PARKS_DECISION candidates
if MAX_TIER >= 2 and any(e["tier"] == 2 for e in edges):
    loops_all, GSTATS["full"] = find_loops(ADJ, "FULL", C["EXHAUSTIVE_MAX_LOOPS"], C["EXHAUSTIVE_WORK"])
    loop_routes += [r for r in loops_all if r["tier"] == 2]
LOOP_METHOD = "public network: %s; full network: %s (%.2f-%.1f mi)" % (
    GSTATS["public"]["method"], GSTATS.get("full", {}).get("method", "not run"), C["LOOP_MIN_MI"], max_mi)
seen_loops = set()
for r in loop_routes:
    k = frozenset(r["eids"])
    if r["mi"] >= C["LOOP_MIN_MI"] and k not in seen_loops:
        seen_loops.add(k)
        add_route(r)
print("   loops kept: %d (public-eligible %d, need Unauthorized %d)" % (
    sum(r["type"] == "LOOP" for r in ROUTES), sum(r["type"] == "LOOP" and r["tier"] <= 1 for r in ROUTES),
    sum(r["type"] == "LOOP" and r["tier"] == 2 for r in ROUTES)))

for nm in sorted({e["pub"] for e in edges if e["kind"] == "TRAIL" and e["pub"] != "Connector" and e["tier"] <= MAX_TIER}):
    eids = [i for i, e in enumerate(edges) if e["kind"] == "TRAIL" and e["pub"] == nm and e["tier"] <= MAX_TIER]
    ns = {n for i in eids for n in (edges[i]["u"], edges[i]["v"])}
    eids += [i for i, e in enumerate(edges) if e["kind"].startswith("GAP") and e["u"] in ns and e["v"] in ns]
    uf2 = UF()
    idx = {n: uf2.add() for n in ns}
    for i in eids:
        uf2.u(idx[edges[i]["u"]], idx[edges[i]["v"]])
    groups = defaultdict(list)
    for i in eids:
        groups[uf2.f(idx[edges[i]["u"]])].append(i)
    gl = sorted(groups.values(), key=lambda g: -sum(edges[i]["len"] for i in g))
    for gi, g in enumerate(gl):
        shape, steps = order_subgraph(edges, g)
        add_route(route_metrics(steps, shape == "LOOP", "NAMED_" + shape,
                                nm + ("" if len(gl) == 1 else " (piece %d of %d)" % (gi + 1, len(gl)))))

for L in LINKS:
    da, db = DEG.get(L["a"], 0), DEG.get(L["b"], 0)
    if L["a"] == L["b"]:
        continue
    if (da == 1) != (db == 1) and L["len"] >= C["SPUR_MIN_MI"] * MI:
        add_route(route_metrics(L["steps"] if db == 1 else [(e, not f) for e, f in reversed(L["steps"])], False, "SPUR"))
    elif da >= 3 and db >= 3 and all(edges[e]["pub"] in ("Connector", None) for e, _ in L["steps"]):
        add_route(route_metrics(L["steps"], False, "CONNECTOR", "Connector"))
print("candidates: %s" % dict(Counter(r["type"] for r in ROUTES)))
check("all routes continuous / closed / no repeated edges", not FAILS, str(FAILS[:5]) if FAILS else "")
finish(9)


# %% ==========================================================================
# CELL 10 - SELECT A RECOMMENDED SET
# =============================================================================
begin(10, "2026-10-20c")
hdr("CELL 10 - RECOMMENDED SET")
def greedy(pool, k, against=()):
    chosen = []
    for r in sorted(pool, key=lambda r: (-r["score"], r["len"])):
        if len(chosen) >= k:
            break
        if all(overlap(r, c) <= C["MAX_OVERLAP"] for c in chosen + list(against)):
            chosen.append(r)
    return chosen
for r in ROUTES:
    r["status"] = "CANDIDATE"
core = []
for cls, lo, hi in C["LENGTH_CLASSES"]:
    for r in greedy([r for r in ROUTES if r["type"] == "LOOP" and r["class"] == cls and r["tier"] <= 1], C["PICK_PER_CLASS"].get(cls, 3)):
        r["status"] = "CORE"
        core.append(r)
for cls, lo, hi in C["LENGTH_CLASSES"]:
    if not any(r["class"] == cls for r in core):
        proven = GSTATS["public"]["method"] == "complete"
        shortcut("NO_%s_PUBLIC_LOOP" % cls.upper(), 0, "No public-eligible %s loop (%.0f-%.0f mi) %s." % (
            cls, lo, min(hi, C["LOOP_MAX_MI"]), "exists on Official/eligible trails (complete search)" if proven
            else "was found (sampled search; may exist)"))
for r in greedy([r for r in ROUTES if r["type"] == "LOOP" and r["tier"] == 2], C["PICK_PARKS_DECISION"], core):
    r["status"] = "PARKS_DECISION"
for r in ROUTES:
    if r["type"] in ("NAMED_LINEAR", "NAMED_LOOP") and r["tier"] <= 1:
        r["status"] = "CORE"
    elif r["type"] in ("NAMED_SYSTEM", "SPUR", "CONNECTOR") and r["tier"] <= 1 and r["status"] == "CANDIDATE":
        r["status"] = "OPTION"
prefix = {"LOOP": "L", "NAMED_LINEAR": "N", "NAMED_LOOP": "N", "NAMED_SYSTEM": "N", "SPUR": "S", "CONNECTOR": "C"}
ROUTES.sort(key=lambda r: ({"CORE": 0, "PARKS_DECISION": 1, "OPTION": 2}.get(r["status"], 3), r["type"], r["mi"]))
cnt = Counter()
for r in ROUTES:
    p = prefix[r["type"]] + ("-" + r["class"][0] if r["type"] == "LOOP" else "")
    cnt[p] += 1
    r["id"] = "%s-%03d" % (p, cnt[p])
REC = [r for r in ROUTES if r["status"] != "CANDIDATE"]
print("recommended by status/type:", dict(Counter((r["status"], r["type"]) for r in REC)))
for r in REC:
    if r["status"] in ("CORE", "PARKS_DECISION") and r["type"] == "LOOP" or r["type"] == "NAMED_LOOP":
        print("%-9s %-14s %-30s %5.2f mi  chg %-2d dec %-2d score %5.1f  %s"
              % (r["id"], r["status"], r["label"][:30], r["mi"], r["changes"], r["decisions"], r["score"], r["flags"]))
poc = [r for r in ROUTES if r["type"] == "LOOP" and abs(r["mi"] - C["POC_LOOP_MI"]) <= 0.08 and "Blue" in r["names"] and "Red" in r["names"]]
print("POC (~%.2f mi Blue+Red loop):" % C["POC_LOOP_MI"], [(r["id"], r["mi"], r["status"]) for r in poc][:5] or "not found")
finish(10)


# %% ==========================================================================
# CELL 11 - WRITE NETWORK, ROUTES, MEMBERS (decisions re-applied by ROUTE_KEY)
# =============================================================================
begin(11, "2026-10-20c")
hdr("CELL 11 - WRITE OUTPUTS")
detach_work_layers()
if not REC:
    raise RuntimeError("No recommended routes in memory - refusing to overwrite the route tables. Check CELLS 9-10.")
def make_fc(name, geom, fields, protected=False):
    p = out_path(name, allow_protected=protected)
    if geom:
        arcpy.management.CreateFeatureclass(WORK_GDB, os.path.basename(p), geom, spatial_reference=WORK_SR)
    else:
        arcpy.management.CreateTable(WORK_GDB, os.path.basename(p))
    add_fields(p, fields)
    return p

DEC_T = gdbp("MCP_Route_Decisions")
if not arcpy.Exists(DEC_T):
    make_fc("MCP_Route_Decisions", None, [("ROUTE_KEY", "TEXT", 16), ("PUBLIC_NAME", "TEXT", 100),
            ("DECISION", "TEXT", 12), ("NOTES", "TEXT", 255), ("UPDATED", "TEXT", 20)], protected=True)
DECISIONS = {r[0]: r[1:] for r in arcpy.da.SearchCursor(DEC_T, ["ROUTE_KEY", "PUBLIC_NAME", "DECISION", "NOTES"])}
print("saved decisions loaded:", len(DECISIONS))

NODE_FC = make_fc("MCP_Net_Nodes", "POINT", [("NODE_ID", "LONG", None), ("DEGREE", "SHORT", None), ("KIND", "TEXT", 14)])
with arcpy.da.InsertCursor(NODE_FC, ["SHAPE@XY", "NODE_ID", "DEGREE", "KIND"]) as cur:
    for n, xy in enumerate(node_xy):
        d = DEG.get(n, 0)
        cur.insertRow((xy, n, d, "CROSSING" if n in crossing_nodes else "JUNCTION" if d >= 3 else
                       "DEAD_END" if d == 1 else "PASS" if d == 2 else "NOT_ROUTABLE"))

EDGE_F = [("EDGE_ID", "LONG", None), ("KIND", "TEXT", 12), ("SRC_GLOBALID", "TEXT", 38), ("PID", "TEXT", 20),
          ("FROM_FT", "DOUBLE", None), ("TO_FT", "DOUBLE", None), ("SRC_NAME", "TEXT", 100), ("PUB_NAME", "TEXT", 100),
          ("DESIG_CLS", "TEXT", 14), ("TIER", "SHORT", None), ("SURFACE", "TEXT", 30), ("DIFFICULTY", "TEXT", 40),
          ("RANK", "TEXT", 20), ("MARKER", "TEXT", 30), ("LEN_FT", "DOUBLE", None), ("GAP_FT", "DOUBLE", None),
          ("FROM_NODE", "LONG", None), ("TO_NODE", "LONG", None)]
EDGE_FC = make_fc("MCP_Net_Edges", "POLYLINE", EDGE_F)
with arcpy.da.InsertCursor(EDGE_FC, ["SHAPE@"] + [f[0] for f in EDGE_F]) as cur:
    for i, e in enumerate(edges):
        cur.insertRow((e["g"], i, e["kind"], e["gid"], e["pid"], e["from_ft"], e["to_ft"], e["name"][:100], e["pub"],
                       e["cls"], e["tier"], e["surf"], str(e["diff"])[:40] if e["diff"] else None, e["rank"][:20],
                       str(e["marker"])[:30] if e["marker"] else None, round(e["len"] / U, 2), e["gap_ft"], e["u"], e["v"]))

ROUTE_F = [("ROUTE_ID", "TEXT", 12), ("ROUTE_KEY", "TEXT", 16), ("STATUS", "TEXT", 16), ("ROUTE_TYPE", "TEXT", 14),
           ("LABEL", "TEXT", 120), ("PUBLIC_NAME", "TEXT", 100), ("DECISION", "TEXT", 12), ("NOTES", "TEXT", 255),
           ("LEN_MI", "DOUBLE", None), ("TRIP_MI", "DOUBLE", None), ("LEN_CLASS", "TEXT", 8), ("TIER", "SHORT", None),
           ("SCORE", "DOUBLE", None), ("NAMES_MIX", "TEXT", 255), ("NAME_CHG", "SHORT", None),
           ("DECISIONS", "SHORT", None), ("SURF_MIX", "TEXT", 120), ("PAVED_PCT", "DOUBLE", None),
           ("DIFFICULTY", "TEXT", 40), ("MARKED_PCT", "DOUBLE", None), ("ACCESS_PCT", "DOUBLE", None), ("FLAGS", "TEXT", 255), ("N_EDGES", "SHORT", None)]
def route_geom(steps, r=None):
    if r is not None and "geom" in r:
        return r["geom"]
    arr = arcpy.Array()
    for e, _ in steps:
        g = edges[e]["g"]
        for k in range(g.partCount):
            arr.add(g.getPart(k))
    pl = arcpy.Polyline(arr, WORK_SR)
    if r is not None:
        r["geom"] = pl
    return pl
def route_row(r):
    dn = DECISIONS.get(rkey(r), (None, None, None))
    return (route_geom(r["steps"], r), r["id"], rkey(r), r["status"], r["type"], r["label"][:120], dn[0], dn[1], dn[2],
            r["mi"], r["trip_mi"], r["class"], r["tier"], r["score"], r["names"][:255], r["changes"], r["decisions"],
            r["surfaces"][:120], r["paved"], str(r["difficulty"])[:40], r["marked_pct"], r["access_pct"], r["flags"][:255], len(r["steps"]))
def write_routes(name, rs):
    fc = make_fc(name, "POLYLINE", ROUTE_F)
    t0 = time.time()
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + [f[0] for f in ROUTE_F]) as cur:
        for n, r in enumerate(rs, 1):
            cur.insertRow(route_row(r))
            if n % 500 == 0:
                print("      %s: %d/%d rows, %.0f s" % (name, n, len(rs), time.time() - t0))
    return fc
for r in REC:
    rkey(r)
REC_FC = write_routes("MCP_Routes_Recommended", REC)
_rec_ids = {id(r) for r in REC}
WRITTEN = REC + sorted([r for r in ROUTES if id(r) not in _rec_ids], key=lambda r: -r["score"])[:max(0, C["MAX_CANDIDATES_WRITTEN"] - len(REC))]
if len(WRITTEN) < len(ROUTES):
    shortcut("CANDIDATES_TRUNCATED", len(ROUTES) - len(WRITTEN), "Only the top %d of %d candidate routes were written to MCP_Route_Candidates (raise MAX_CANDIDATES_WRITTEN to keep more)." % (len(WRITTEN), len(ROUTES)))
    print("   writing %d of %d candidates (top scoring + all recommended)" % (len(WRITTEN), len(ROUTES)))
for r in WRITTEN:
    rkey(r)
ALL_FC = write_routes("MCP_Route_Candidates", WRITTEN)
MEM_F = [("ROUTE_ID", "TEXT", 12), ("ROUTE_KEY", "TEXT", 16), ("SEQ", "SHORT", None), ("EDGE_ID", "LONG", None),
         ("EDGE_KIND", "TEXT", 12), ("SRC_GLOBALID", "TEXT", 38), ("FROM_FT", "DOUBLE", None), ("TO_FT", "DOUBLE", None),
         ("DIRECTION", "TEXT", 1), ("PUB_NAME", "TEXT", 100), ("LEN_FT", "DOUBLE", None)]
MEM_T = make_fc("MCP_Route_Members", None, MEM_F)
with arcpy.da.InsertCursor(MEM_T, [f[0] for f in MEM_F]) as cur:
    for r in WRITTEN:
        for s, (e, fwd) in enumerate(r["steps"]):
            ed = edges[e]
            cur.insertRow((r["id"], rkey(r), s + 1, e, ed["kind"], ed["gid"], ed["from_ft"], ed["to_ft"],
                           "F" if fwd else "R", ed["pub"], round(ed["len"] / U, 2)))
check("route geometry = sum of members", all(abs(route_geom(r["steps"], r).length - r["len"]) <= 0.005 * r["len"] + SNAP for r in REC))
write_csv("routes_recommended.csv", [{k: v for k, v in r.items() if k not in ("steps", "eids", "geom")} for r in REC])
write_csv("gap_joins.csv", [{"edge": i, "kind": e["kind"], "why": e["name"], "gap_ft": round(e["gap_ft"], 2),
                             "x": round(node_xy[e["u"]][0], 1), "y": round(node_xy[e["u"]][1], 1)}
                            for i, e in enumerate(edges) if e["kind"].startswith("GAP")])
for p in (NODE_FC, EDGE_FC, REC_FC, ALL_FC, MEM_T):
    print("   %-28s %s rows" % (os.path.basename(p), count(p)))
finish(11)


# %% ==========================================================================
# CELL 12 - WRITE-UP, REVIEW MAP, PASTE-BACK SUMMARY
# =============================================================================
begin(12, "2026-10-20c")
hdr("CELL 12 - WRITE-UP + REVIEW MAP")
tbl = ["| ID | Status | Type | Label | Miles | Names | Surface | Difficulty | Name changes | Flags |", "|" + "---|" * 10]
tbl += ["| %s | %s | %s | %s | %.2f | %s | %s | %s | %d | %s |" % (r["id"], r["status"], r["type"], r["label"], r["trip_mi"],
        r["names"], r["surfaces"], r["difficulty"], r["changes"], r["flags"] or "-") for r in REC]
md = "\n".join([
    "# Mercer County Park - Proposed Public Trail Routes (DRAFT for Parks review)",
    "Generated %s. GIS proposals only; Mercer County Parks decides names, designations and publication." % RUN_TS, "",
    "## Data",
    "- Source: %s (local snapshot `SRC_Trails`, %d features). Duplicate service `Assets/MCPC_Trails` verified identical (602/602)." % (TRAILS_SRC, count(SRC_TRAILS)),
    "- Mercer County Park subset: %d segments, %.2f mi. By designation: %s." % (len(feats), src_len / MI, ", ".join("%s %.2f mi" % kv for kv in sorted(cls_mi.items()))),
    "- Network: %d edges, %d nodes." % (len(edges), len(node_xy)), "",
    "## Method",
    "1. Local copy of the Parks segment layer; park selected by Park attribute and boundary, compared.",
    "2. Derived junction network: splits at endpoints, T-junctions%s; source geometry untouched." % (" and crossings" if C["CROSSINGS_AS_JUNCTIONS"] else ""),
    "3. Gaps repaired with explicit, flagged connector edges.",
    "4. Loops: %s; plus existing named trails, out-and-backs and connectors." % LOOP_METHOD,
    "5. Scored for wayfinding (name changes, decision points, blazed/marked share, primary trails, surface consistency) and data confidence; non-overlapping set chosen per length class.",
    "6. Validated continuity, closure, no repeated segments, length conservation.", "",
    "## Shortcuts and limitations", *["- **%s** (%s): %s" % (s["shortcut"], s["count"], s["detail"]) for s in SHORTCUTS], "",
    "## Recommended routes", *tbl, "",
    "## Parks decisions needed",
    "- PARKS_DECISION loops require Unauthorized segments.",
    "- Official names for every route (labels are placeholders).", "",
    "## QA checks", *["- %s: %s %s" % (c["result"], c["check"], c["detail"]) for c in CHECKS]])
WRITEUP = os.path.join(REPORT_DIR, "MCP_Route_Writeup.md")
with open(WRITEUP, "w", encoding="utf-8") as fh:
    fh.write(md)

try:
    found = [m for m in aprx.listMaps() if m.name == C["REVIEW_MAP_NAME"]]
    rm = found[0] if found else aprx.createMap(C["REVIEW_MAP_NAME"])
    for lyr in rm.listLayers():
        if not lyr.isBasemapLayer:
            rm.removeLayer(lyr)
    if not any(l.isBasemapLayer for l in rm.listLayers()):
        try:
            rm.addBasemap("Imagery Hybrid")
        except Exception:
            pass
    for p in [gdbp("MCP_Boundary"), PHYS, EDGE_FC, NODE_FC, REC_FC]:   # candidates layer left out on purpose (heavy to draw)
        if arcpy.Exists(p):
            rm.addDataFromPath(p)
    rm.addDataFromPath(DEC_T)
    print("Review map '%s' ready. Open it from the Catalog pane (Maps)." % rm.name)
except Exception as e:
    print("Review map skipped:", e)

print("\n" + "#" * 78 + "\nPASTE-BACK SUMMARY (%s)\n" % RUN_TS + "#" * 78)
print("Source: %s | park filter: %s | selection %s" % (os.path.basename(TRAILS_SRC.rstrip("/0")), twhere, dict(tally)))
print("Segments %d, %.2f mi | by class %s | manual edits %s" % (len(feats), src_len / MI, {k: round(v, 2) for k, v in cls_mi.items()}, dict(EDITS) or "none"))
print("Network: %d edges, %d nodes, AUTO %d, ASSUMED %d, crossings %d" % (len(edges), len(node_xy), n_auto, n_assumed, n_cross))
print("Loops %d (<= %.1f mi, %s) | candidates %d | recommended %d | %s" % (len(loop_routes), max_mi, LOOP_METHOD.split(" (")[0], len(ROUTES), len(REC), dict(Counter(r["status"] for r in ROUTES))))
print("Checks:", dict(Counter(c["result"] for c in CHECKS)), [c["check"] for c in CHECKS if c["result"] == "FAIL"])
print("Shortcuts:", "; ".join("%s=%s" % (s["shortcut"], s["count"]) for s in SHORTCUTS))
print("POC:", [(r["id"], r["mi"]) for r in poc] or "not found")
print("Write-up:", WRITEUP)
finish(12)


# %% ==========================================================================
# CELL 13 - ROUTE BUILDER (on demand)
#   In the review map, select edges in MCP_Net_Edges (Select tool, Shift to add),
#   set C["BUILDER_ROUTE_NAME"], run this cell. The selection must form one
#   continuous loop or line. Saved to MCP_Routes_Custom (never overwritten).
# =============================================================================
begin(13, "2026-10-20c")
hdr("CELL 13 - ROUTE BUILDER")
def selected_edge_ids():
    for mp in aprx.listMaps():
        for lyr in mp.listLayers():
            try:
                if lyr.supports("DATASOURCE") and os.path.normcase(lyr.dataSource) == os.path.normcase(EDGE_FC):
                    sel = lyr.getSelectionSet()
                    if sel:
                        return [r[0] for r in arcpy.da.SearchCursor(lyr, ["EDGE_ID"])]
            except Exception:
                pass
    return []
sel_ids = selected_edge_ids()
if not sel_ids:
    print("No edges selected in an MCP_Net_Edges layer. Select edges on the map, then rerun this cell.")
else:
    shape, steps = order_subgraph(edges, sel_ids)
    if shape == "SYSTEM":
        deg = Counter()
        for i in sel_ids:
            deg[edges[i]["u"]] += 1
            deg[edges[i]["v"]] += 1
        bad = [n for n, d in deg.items() if d > 2]
        ends = [n for n, d in deg.items() if d == 1]
        print("Selection is not one continuous route: %d branch node(s) %s, %d loose end(s)."
              % (len(bad), bad[:6], len(ends)))
        print("Fix: deselect side branches; for a gap, also select the GAP_ edge between the pieces (or snap the")
        print("trails in MCP_PhysicalTrails and rerun from CELL 5).")
    else:
        r = route_metrics(steps, shape == "LOOP", "CUSTOM_" + shape, C["BUILDER_ROUTE_NAME"])
        r["status"], r["id"] = "CUSTOM", "U-" + rkey(r)[:6]
        CUST = gdbp("MCP_Routes_Custom")
        if not arcpy.Exists(CUST):
            make_fc("MCP_Routes_Custom", "POLYLINE", ROUTE_F, protected=True)
        existing = {k for (k,) in arcpy.da.SearchCursor(CUST, ["ROUTE_KEY"])}
        if rkey(r) in existing:
            print("This exact route is already saved (ROUTE_KEY %s)." % rkey(r))
        else:
            with arcpy.da.InsertCursor(assert_writable(CUST), ["SHAPE@"] + [f[0] for f in ROUTE_F]) as cur:
                row = list(route_row(r))
                row[6] = row[6] or C["BUILDER_ROUTE_NAME"]
                cur.insertRow(row)
            print("Saved %s '%s': %s, %.2f mi | %s | surfaces %s | flags %s"
                  % (r["id"], C["BUILDER_ROUTE_NAME"], shape, r["mi"], r["names"], r["surfaces"], r["flags"] or "none"))
            print("Add MCP_Routes_Custom to the map (once) to see it.")
finish(13)


# %% ==========================================================================
# CELL 14 - SAVE NAMES/DECISIONS + BUILD PUBLIC LAYER (on demand)
#   In MCP_Routes_Recommended or MCP_Routes_Custom, fill PUBLIC_NAME and set
#   DECISION to KEEP / DROP / REVISE (attribute table), save edits, run this.
# =============================================================================
begin(14, "2026-10-20c")
hdr("CELL 14 - SAVE DECISIONS + PUBLIC LAYER")
harvest = {}
for fc in (gdbp("MCP_Routes_Recommended"), gdbp("MCP_Routes_Custom")):
    if arcpy.Exists(fc):
        for k, nm, dc, nt in arcpy.da.SearchCursor(fc, ["ROUTE_KEY", "PUBLIC_NAME", "DECISION", "NOTES"]):
            if not (is_blank(nm) and is_blank(dc) and is_blank(nt)):
                harvest[k] = (nm, (dc or "").upper().strip() or None, nt)
with arcpy.da.UpdateCursor(assert_writable(DEC_T), ["ROUTE_KEY", "PUBLIC_NAME", "DECISION", "NOTES", "UPDATED"]) as cur:
    for row in cur:
        if row[0] in harvest:
            row[1:4] = harvest.pop(row[0])
            row[4] = RUN_TS
            cur.updateRow(row)
with arcpy.da.InsertCursor(DEC_T, ["ROUTE_KEY", "PUBLIC_NAME", "DECISION", "NOTES", "UPDATED"]) as cur:
    for k, v in harvest.items():
        cur.insertRow((k,) + v + (RUN_TS,))
DECISIONS = {r[0]: r[1:] for r in arcpy.da.SearchCursor(DEC_T, ["ROUTE_KEY", "PUBLIC_NAME", "DECISION", "NOTES"])}
print("decisions stored:", len(DECISIONS), dict(Counter(v[1] for v in DECISIONS.values())))

PUB_F = [("PUBLIC_NAME", "TEXT", 100), ("ROUTE_FORM", "TEXT", 16), ("LEN_MI", "DOUBLE", None), ("TRIP_MI", "DOUBLE", None),
         ("SURFACE", "TEXT", 120), ("PAVED_PCT", "DOUBLE", None), ("DIFFICULTY", "TEXT", 40), ("ROUTE_ID", "TEXT", 12),
         ("ROUTE_KEY", "TEXT", 16), ("FLAGS", "TEXT", 255)]
form = lambda t: "Loop" if "LOOP" in t else "Out-and-back" if t == "SPUR" else "Connector" if t == "CONNECTOR" else "Trail"
keep = []
for fc in (gdbp("MCP_Routes_Recommended"), gdbp("MCP_Routes_Custom")):
    if arcpy.Exists(fc):
        for row in arcpy.da.SearchCursor(fc, ["SHAPE@", "ROUTE_KEY", "ROUTE_TYPE", "LEN_MI", "TRIP_MI", "SURF_MIX",
                                              "PAVED_PCT", "DIFFICULTY", "ROUTE_ID", "FLAGS", "LABEL"]):
            dn = DECISIONS.get(row[1])
            if dn and dn[1] == "KEEP":
                keep.append((row[0], dn[0] or row[10], form(row[2]), row[3], row[4], row[5], row[6], row[7], row[8], row[1], row[9]))
if keep:
    PUB = make_fc("MCP_Public_Routes", "POLYLINE", PUB_F)
    with arcpy.da.InsertCursor(PUB, ["SHAPE@"] + [f[0] for f in PUB_F]) as cur:
        for k in keep:
            cur.insertRow(k)
    print("MCP_Public_Routes: %d routes (symbolize by PUBLIC_NAME; label with PUBLIC_NAME + LEN_MI)." % len(keep))
else:
    print("No routes marked KEEP yet. Set DECISION = KEEP (and PUBLIC_NAME) in the attribute table, save, rerun this cell.")
finish(14)


