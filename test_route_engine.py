"""Tests for the pure-Python route engine (notebook Cells 7-10) - no arcpy needed.
Run:  python tests/test_route_engine.py      (exit code 0 = all passed)"""
import sys, os, re, time, types, random, io, contextlib
from collections import defaultdict, Counter

NB = os.path.join(os.path.dirname(__file__), "..", "notebooks", "MCP_Trails_Complete_Notebook.py")
sys.modules["arcpy"] = types.ModuleType("arcpy")            # notebook imports arcpy; engine cells never call it
SRC = open(NB, encoding="utf-8").read()
CELLS = {int(m.group(1)): c for c in re.split(r"(?=# %% =+\n# CELL \d+ )", SRC)
         for m in [re.search(r"# CELL (\d+) ", c)] if m}
UF_SRC = re.search(r"class UF:.*?(?=\ndef cell)", CELLS[6], re.S).group(0)

def run_engine(edges):
    ns = {}
    exec(CELLS[0], ns)
    ns.update(hdr=lambda t: None, lap=lambda l: None, _chk=[], _sc=[], U=1.0, MI=5280.0, MAX_TIER=2,
              crossing_nodes=set(), edges=edges, DIFF_F="Difficulty",
              is_blank=lambda v: v is None or (isinstance(v, str) and not v.strip()),
              diff_rank=lambda v: {"Easy": 1, "Moderate": 2, "Difficult": 3}.get(v, 0))
    ns["check"] = lambda n, ok, d="": ns["_chk"].append((n, ok, d))
    ns["shortcut"] = lambda n, c, d: ns["_sc"].append((n, c))
    exec(UF_SRC, ns)
    t = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        for n in (7, 8, 9, 10):
            exec(CELLS[n], ns)
    return ns, time.time() - t

def synth(N, seed, tiers=(70, 15, 15), conn_tiers=(40, 20, 40), keep=0.95):
    rng = random.Random(seed); edges = []; nid = [N * N]
    def add(u, v, L, name, w):
        t = rng.choices([0, 1, 2], w)[0]
        m = nid[0]; nid[0] += 1
        for a, b, l in ((u, m, L * .4), (m, v, L * .6)):          # pass-through nodes exercise link compression
            edges.append(dict(kind="TRAIL", pid="p%d" % len(edges), gid="G%d" % len(edges), from_ft=0.0, to_ft=l,
                              g=None, len=l, u=a, v=b, name=name, pub="Connector" if "conn" in name else name,
                              cls=("OFFICIAL", "UNKNOWN", "UNAUTHORIZED")[t], tier=t,
                              surf=rng.choice(["Paved", "Natural"]), diff=rng.choice(["Easy", "Moderate", None]),
                              rank=rng.choice(["Primary", "Secondary"]), marker=rng.choice(["Red", None])))
    for r in range(N):
        for c in range(N):
            i = r * N + c
            if c + 1 < N and rng.random() < keep:
                add(i, i + 1, rng.uniform(500, 900), ("Red Trail", "Blue Trail", "Paved Trail")[r % 3], tiers)
            if r + 1 < N and rng.random() < keep:
                add(i, i + N, rng.uniform(500, 900), "MCP_connector_%02d" % c, conn_tiers)
    return edges

def brute(links, max_len):
    ladj = defaultdict(list)
    for lid, L in enumerate(links):
        ladj[L["a"]].append((lid, L["b"]))
        if L["a"] != L["b"]:
            ladj[L["b"]].append((lid, L["a"]))
    out = set()
    for s in sorted(ladj):
        def dfs(u, L, ps, vis):
            for lid, v in ladj[u]:
                if lid in ps: continue
                L2 = L + links[lid]["len"]
                if L2 > max_len: continue
                if v == s: out.add(frozenset(ps | {lid})); continue
                if v < s or v in vis: continue
                dfs(v, L2, ps | {lid}, vis | {v})
        dfs(s, 0.0, frozenset(), {s})
    return out

def invariants(ns):
    R, E, C = ns["ROUTES"], ns["edges"], ns["C"]
    assert not ns["FAILS"], ns["FAILS"]
    assert all(ok for _, ok, _ in ns["_chk"]), ns["_chk"]
    for r in R:
        if r["type"] != "NAMED_SYSTEM":
            assert ns["is_continuous"](E, r["steps"], r["closed"]), r["label"]
    loops = [r for r in R if r["type"] == "LOOP"]
    assert len({frozenset(r["eids"]) for r in loops}) == len(loops), "duplicate loops"
    assert all(C["LOOP_MIN_MI"] <= r["mi"] <= C["LOOP_MAX_MI"] + 1e-9 for r in loops)
    for cls, _, _ in C["LENGTH_CLASSES"]:
        core = [r for r in loops if r["status"] == "CORE" and r["class"] == cls]
        assert all(r["tier"] <= 1 for r in core)
        for i in range(len(core)):
            for j in range(i + 1, len(core)):
                assert ns["overlap"](core[i], core[j]) <= C["MAX_OVERLAP"] + 1e-9
    assert all(r["tier"] == 2 for r in R if r["status"] == "PARKS_DECISION")

def test_complete_search_matches_brute_force():
    ns, _ = run_engine(synth(5, 2, keep=0.85))
    invariants(ns)
    E, MAXL, MINL = ns["edges"], ns["C"]["LOOP_MAX_MI"] * 5280.0, ns["C"]["LOOP_MIN_MI"] * 5280.0
    def expand(links, sets): return {frozenset(e for l in c for e, _ in links[l]["steps"]) for c in sets}
    pub = ns["build_links"](E, ns["build_adj"](E, 1))
    ref = {k for k in expand(pub, brute(pub, MAXL)) if sum(E[e]["len"] for e in k) >= MINL}
    got = {frozenset(r["eids"]) for r in ns["ROUTES"] if r["type"] == "LOOP" and r["tier"] <= 1}
    assert got == ref, (len(got), len(ref))
    full = ns["build_links"](E, ns["build_adj"](E, 2))
    if ns["GSTATS"].get("full", {}).get("method") == "complete":
        ref2 = {k for k in expand(full, brute(full, MAXL))
                if max(E[e]["tier"] for e in k) == 2 and sum(E[e]["len"] for e in k) >= MINL}
        got2 = {frozenset(r["eids"]) for r in ns["ROUTES"] if r["type"] == "LOOP" and r["tier"] == 2}
        assert got2 == ref2, (len(got2), len(ref2))

def test_basis_equals_cyclomatic_number():
    ns, _ = run_engine(synth(12, 4))
    st = ns["GSTATS"]["full"]
    if st["method"] == "basis+beam":
        lk = ns["build_links"](ns["edges"], ns["build_adj"](ns["edges"], 2))
        nodes = {L["a"] for L in lk} | {L["b"] for L in lk}
        uf = ns["UF"](); idx = {n: uf.add() for n in nodes}
        for L in lk: uf.u(idx[L["a"]], idx[L["b"]])
        assert st["basis"] == len(lk) - len(nodes) + len({uf.f(i) for i in idx.values()})

def test_long_loops_recommended_when_they_exist():
    ns, _ = run_engine(synth(9, 3, tiers=(100, 0, 0), conn_tiers=(100, 0, 0)))
    invariants(ns)
    assert any(r["status"] == "CORE" and r["class"] == "Long" for r in ns["ROUTES"])

def test_runtime_bounded_on_dense_networks():
    for N in (14, 20):
        ns, t = run_engine(synth(N, N))
        invariants(ns)
        assert t < 60, "engine took %.1f s on %dx%d" % (t, N, N)

if __name__ == "__main__":
    failed = 0
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        t = time.time()
        try:
            fn(); print("PASS  %-45s %.1f s" % (name, time.time() - t))
        except Exception as e:
            failed += 1; print("FAIL  %-45s %r" % (name, e))
    sys.exit(1 if failed else 0)
