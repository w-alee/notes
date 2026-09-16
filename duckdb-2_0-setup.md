Use your **current environment**. Do not run `uv venv`.

DuckDB 2.0 is still a **pre-release**. uv ignores pre-releases unless you allow them. Official package command remains `duckdb --pre`.

---

### 1. Upgrade DuckDB in the env you already have

In PowerShell, from the project/folder that already uses this Python:

```powershell
python -c "import sys,duckdb; print(sys.executable); print(duckdb.__version__)"
```

Then install 2.0 into **that same interpreter**:

```powershell
uv pip install --python (Get-Command python).Source --upgrade --prerelease allow duckdb
```

If that env already has `VIRTUAL_ENV` set (existing `.venv` activated), this is enough:

```powershell
uv pip install --upgrade --prerelease allow duckdb
```

If this is a **uv project** (`pyproject.toml` present) and you want the lockfile updated too:

```powershell
uv add --active --prerelease allow --upgrade-package duckdb "duckdb>=2.0.0.dev0"
```

`--active` targets the env you are already using. It does not create a new one.

Confirm:

```powershell
python -c "import duckdb; print(duckdb.__version__)"
```

You want `2.0.0.dev…`, not `1.5.4`.

Marimo can stay as-is if it is already installed. If SQL cells break after the swap:

```powershell
uv pip install --upgrade --prerelease allow "marimo[sql]" "polars[pyarrow]"
```

---

### 2. Upgrade the `.duckdb` files

v2.0’s default storage is **v2.0.0**. 2.0 should **read** 1.5.4 files. It will **not** rewrite them in place. Files written with v2 storage will not open in 1.5.4. Copy first.

```powershell
copy C:\data\my.duckdb C:\data\my.duckdb.bak
copy C:\data\my.duckdb C:\data\my_v2.duckdb
```

Convert the copy (not the original):

```powershell
python -c @"
import duckdb
con = duckdb.connect()
con.execute(r""ATTACH 'C:\data\my.duckdb.bak' AS old (READ_ONLY)"")
con.execute(r""ATTACH 'C:\data\my_v2.duckdb' AS new"")
con.execute('COPY FROM DATABASE old TO new')
print(con.execute('SELECT database_name, tags FROM duckdb_databases()').fetchall())
"@
```

Point Marimo / Python at `my_v2.duckdb`:

```python
import duckdb
conn = duckdb.connect(r"C:\data\my_v2.duckdb")
```

If `COPY FROM DATABASE` fails on this alpha, use export/import against an **empty** target file:

```powershell
python -c @"
import duckdb
old = duckdb.connect(r'C:\data\my.duckdb.bak', read_only=True)
old.execute(r""EXPORT DATABASE 'C:\data\duckdb_export'"")
new = duckdb.connect(r'C:\data\my_v2.duckdb')
new.execute(r""IMPORT DATABASE 'C:\data\duckdb_export'"")
"@
```

---

### 3. Rollback the package only

```powershell
uv pip install --python (Get-Command python).Source "duckdb==1.5.4"
```

Keep `my.duckdb.bak` until you have run your real Snowflake-derived queries on the v2 file. Preview builds are not production-stable. Official 2.0.0 is still scheduled for 21 Oct 2026.
