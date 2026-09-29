I’ll write a Dash-Cytoscape app that reads DuckDB through Polars only, then give setup and run steps.The family-options loader has a leftover parameterized query. I’ll remove it so the app stays pandas-free and runnable.Use Dash Cytoscape as the UI and DuckDB → Arrow → Polars as the data path. Do not import pandas. dash_table accepts Polars to_dicts() directly.
The app is in dash_cytoscape_family_explorer.py. It loads a Phase 2 family, draws directed transfer edges, colors write-off BANs red, and shows wo_lineage when you click a node.
1. Install
pip install dash dash-cytoscape duckdb polars pyarrow
Do not install pandas for this app. DuckDB returns Arrow; Polars consumes Arrow.
2. Prerequisites in DuckDB
The script expects these Phase 1–3 tables in your_extract.duckdb:
	•	ban_family
	•	transfer_edge
	•	ban_centrality (optional; in/out degree and WO dollars fall back to 0)
	•	wo_lineage (optional for the click table; dropdown still works)
Change DB_PATH at the top of the file to your .duckdb path.
3. How data is loaded (no pandas)
def query_pl(con, sql: str) -> pl.DataFrame:
    return pl.from_arrow(con.execute(sql).arrow())
Use iter_rows(named=True) or to_dicts() when Dash needs Python objects. Never call .df().
4. Run
python dash_cytoscape_family_explorer.py
Open http://127.0.0.1:8050.
	•	Dropdown lists families of size 2–800, largest current-window equipment WO first.
	•	Red node = current-window equipment write-off on that BAN.
	•	Arrow = at least one CTN moved from_ban → to_ban.
	•	Click a BAN to see degree, WO dollars, and lineage rows.
5. Why the node cap exists
Cytoscape.js runs in the browser. A family larger than a few hundred BANs becomes a hairball. MAX_NODES = 800 refuses those families. For a large community, filter first in DuckDB (one agent, one address_key, or WO-touched BANs only), then point the query at that slice.
6. If a table name differs
Edit the SQL inside load_family_options, family_elements, and ban_lineage. Keep the same columns: family_id, ban, from_ban, to_ban, wo_amt, path_type.
7. Optional next controls
Once this runs, add another dropdown for community_id from ban_community, or a checklist for path_type. Keep those filters in SQL and return a Polars frame. Do not build a second in-memory NetworkX copy unless you are exporting GEXF for Gephi.
