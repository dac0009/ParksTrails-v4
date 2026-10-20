# ParksTrails-v4 — Mercer County Park public trail routes

Turns the Mercer County Park Commission trail-segment layer into a clean set of visitor routes (loops, named trails, out-and-backs, connectors) for a public trail map. GIS proposes the routes; Mercer County Parks decides names, designations and what gets published.

## Run (ArcGIS Pro)
1. Open the project and sign in to the county portal.
2. Create a Notebook. Paste **all** cells of `MCP_Trails_Complete_Notebook.py` in order and run Cells 0–12 in order. Each cell prints its run time. Cells check the notebook version and refuse to run if an earlier cell failed, so a partial run can't overwrite good results.
3. Review in the **MCP Trail Routes** map. Draft write-up: `MCP_Reports/MCP_Route_Writeup.md`.

Work data goes to `C:\GIS\MCP_ParksTrails` (outside OneDrive). Source services are read only.

## Hands-on steps
- **Fix geometry:** edit `MCP_PhysicalTrails` (snapping on), then rerun from Cell 5. Edits are kept and reported.
- **Custom route:** select edges in `MCP_Net_Edges`, set `BUILDER_ROUTE_NAME`, run Cell 13.
- **Names and decisions:** fill in `PUBLIC_NAME` and set `DECISION` (KEEP / DROP / REVISE) in the route tables, then run Cell 14 to build `MCP_Public_Routes`. Decisions survive reruns.

## Tests
`python test_route_engine.py` runs the route engine (Cells 7–10) without arcpy:
- loop search checked against brute force;
- cycle basis checked against the network's independent-loop count;
- route validity and overlap rules;
- Long loops found when they exist;
- run-time bound on dense networks;
- stage guards: a failed cell blocks later cells, and mixed notebook versions are refused.

These tests run on every push once `tests.yml` is at `.github/workflows/tests.yml`.

Don't commit geodatabases, exports or reports: they contain Parks data.

## Data notes
- Parks' `Length` field is in Web Mercator meters, about 31% longer than true length. Routes use true geodesic length.
- `MCPC_Park_Trails` and `MCPC_Trails` are identical services (602/602 features); `MCPC_Park_Trails` is the source.
- Trail segments use the Park code `MCP`.
