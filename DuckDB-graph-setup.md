Use **Onager** on DuckDB 2.0-alpha. There is no core DuckDB graph database. The official property-graph option is **DuckPGQ**, and current DuckDB docs say it is **not available on 1.5.x** and to stay on **v1.4.4**. Community binaries are also published per engine version; the public list is for **v1.5.5**, not 2.0-alpha.

| Extension | What it is | Fit on 2.0-alpha |
|---|---|---|
| **onager** | Graph *analytics* over edge tables: PageRank, Louvain, Dijkstra, components, etc. No custom graph SQL. | Best first try. Table functions only, so no parser fork. |
| **duckpgq** | Real property-graph layer + SQL/PGQ `MATCH` / shortest path. Official DuckDB graph guide. | Do not expect a working 2.0-alpha community build today. |
| **duckgql** | ISO GQL. Targets DuckDB **v1.5.5**. | Same version-lock problem. |

Onager’s own docs: use DuckPGQ when you need a property-graph model; use Onager when you need algorithms on tables you already have. That matches your Snowflake → `.duckdb` workflow.

---

### Install from Python (no CLI)

Same session that already has DuckDB 2.0:

```python
import duckdb

con = duckdb.connect(r"C:\data\my_v2.duckdb")  # or duckdb.connect()

con.install_extension("onager", repository="community")
con.load_extension("onager")

print(con.execute("SELECT onager_version()").fetchall())
```

Equivalent SQL if you prefer `con.execute(...)`:

```sql
INSTALL onager FROM community;
LOAD onager;
```

`LOAD` is per connection. `INSTALL` is once per machine. In Marimo, put the install/load in the same cell that creates `conn`.

Smoke test:

```python
con.execute("""
CREATE TABLE edges AS
SELECT * FROM (VALUES (1::BIGINT, 2::BIGINT), (2, 3), (3, 1), (3, 4)) t(src, dst)
""")

print(con.execute("""
SELECT node_id, round(rank, 4) AS rank
FROM onager_ctr_pagerank((SELECT src, dst FROM edges))
ORDER BY rank DESC
""").fetchdf())
```

---

### If `INSTALL` fails on 2.0-alpha

That is expected. Extensions are compiled for a specific DuckDB version/platform (`windows_amd64` + `v2.0.0-alpha…`). 2.0-alpha community builds often do not exist yet.

Do **not** load a 1.5.5 `onager` / `duckpgq` binary into 2.0.

Options then:

1. Keep graph work on a **1.4.4 LTS** env and DuckPGQ (`INSTALL duckpgq FROM community`).
2. Stay on 2.0 and use recursive CTEs / joins on your edge tables (no extension).
3. Recheck after 2.0.0 GA (planned 21 Oct 2026), when community CI usually publishes matching Windows wheels.

Python install for DuckPGQ, same pattern, for when a 2.0 build exists:

```python
con.install_extension("duckpgq", repository="community")
con.load_extension("duckpgq")
```

That is the documented Python API. No CLI.
