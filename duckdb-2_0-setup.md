Pin DuckDB with an explicit pre-release specifier and allow pre-releases **only for that package**. That is the current uv-supported way to land `2.0.0.dev*` without opening every other dependency to alphas. Latest 2.0 wheel on PyPI as of 12 Sep 2026 is `2.0.0.dev2609121639`. Official install path is still `duckdb --pre`.

Replace your workspace `pyproject.toml` with this:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "daily-projected"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.12"

dependencies = [
    # --- core dataframe / arrays -------------------------------------------------
    "polars[pyarrow]",
    "pyarrow",
    "numpy",
    "python-dateutil",
    # --- machine learning --------------------------------------------------------
    "lightgbm",
    "scikit-learn",
    # --- visualization -----------------------------------------------------------
    "plotly",
    "matplotlib",
    "seaborn",
    "plotnine",
    "vl-convert-python",
    # --- snowflake ---------------------------------------------------------------
    "snowflake-snowpark-python",
    "snowflake-connector-python[secure-local-storage]",
    "snowflake-sqlalchemy",
    # --- duckdb / sql ------------------------------------------------------------
    # 2.0 is preview (Cyanoptera). Explicit .dev pin is required so uv
    # will take 2.0.0.dev* instead of stable 1.5.5.
    "duckdb>=2.0.0.dev0",
    "duckdb-engine",
    "sqlalchemy",
    # --- notebooks (marimo-first) ------------------------------------------------
    "marimo[recommended,sql]",
    "ipython",
    "ipywidgets",
    # --- cli / io / platform -----------------------------------------------------
    "rich",
    "pyyaml",
    "holidays",
    "xlsxwriter",
    "azure-functions",
    "shiny>=1.7.0",
]

[project.scripts]
dpwo-export = "dpwo.cli.commands.snowflake_to_local:main"
dpwo-predict = "dpwo.cli.commands.predict_to_local:main"
dpwo-nav = "dpwo.cli.nav:main"
dpwo-studio = "dpwo.studio.launch:main"
dpwo-migrate-bill-cycle = "dpwo.cli.commands.migrate_bill_cycle_parquet:main"

[dependency-groups]
dev = [
    "ipykernel",
    "pytest",
    "ruff",
    "mypy",
    "types-pyyaml",
]

[tool.hatch.build.targets.wheel]
packages = ["src/dpwo"]

[tool.uv]
# Allow pre-releases for DuckDB only. Everything else stays on stables.
prerelease-package = { duckdb = "allow" }

[tool.ruff]
target-version = "py312"
line-length = 99
src = ["src"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.ruff.lint.isort]
known-first-party = ["dpwo"]

[tool.ruff.lint.per-file-ignores]
"notebooks/*.py" = ["B018", "F841"]
"apps/*.py" = ["B018", "F841"]

[tool.pytest.ini_options]
testpaths = ["src/scripts/tests"]

[tool.mypy]
python_version = "3.12"
files = ["src/dpwo"]
ignore_missing_imports = true

[tool.marimo.display]
custom_css = ["src/config/marimo_brand.css"]

[tool.marimo.formatting]
line_length = 199

[tool.marimo.package_management]
manager = "uv"

[tool.marimo.runtime]
default_sql_output = "polars"

[tool.marimo.datasources]
auto_discover_schemas = "auto"
auto_discover_tables = "auto"
auto_discover_columns = false
```

### Create the new workspace `.venv`

In the project root (same folder as this file):

```powershell
# optional: keep the old env so 1.5.4 work is recoverable
if (Test-Path .venv) { Rename-Item .venv .venv-15 }

uv lock --upgrade-package duckdb
uv sync --group dev
```

`uv sync` creates `.venv` from this project. Do not run `uv venv` first unless you want an empty env.

Confirm:

```powershell
uv run python -c "import duckdb; print(duckdb.__version__); print(getattr(duckdb,'version',lambda: None)())"
```

Expect `2.0.0.dev…` / `2.0.0-alpha…`, not `1.5.4`.

### `.duckdb` files

2.0 can read 1.5.4 files. It does not rewrite them in place. New files use storage v2.0.0 and will not open in 1.5.4. Copy first, then convert:

```powershell
uv run python -c @"
import duckdb
con = duckdb.connect()
con.execute(r""ATTACH 'C:\data\my.duckdb' AS old (READ_ONLY)"")
con.execute(r""ATTACH 'C:\data\my_v2.duckdb' AS new"")
con.execute('COPY FROM DATABASE old TO new')
print(con.execute('SELECT database_name, tags FROM duckdb_databases()').fetchall())
"@
```

Point Marimo at `my_v2.duckdb`. Keep the 1.5.4 original until your Snowflake-derived queries pass.

If `uv sync` fails on `duckdb-engine`, drop that line temporarily and use `duckdb.connect()` directly. That package last published `0.17.0` in Mar 2025 and is not guaranteed against DuckDB 2.0.
