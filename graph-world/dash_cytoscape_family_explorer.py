"""
Transfer explorer: Dash Cytoscape + DuckDB + Polars. No pandas.

Edit APP, TABLES, and COLS only. Everything else reads those dicts
and the live DuckDB catalog. Missing optional tables do not crash.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import dash_cytoscape as cyto
import duckdb
import polars as pl
from dash import Dash, Input, Output, callback, dash_table, dcc, html

# =============================================================================
# EDIT THESE DICTS
# =============================================================================

APP: dict = {
    "db_path": "your_extract.duckdb",
    "host": "127.0.0.1",
    "port": 8050,
    "max_nodes": 800,
    "family_option_limit": 300,
    "lookback_days": 365,
    "lineage_row_limit": 50,
    "occupancy_row_limit": 50,
}

# logical key -> physical DuckDB table. required=False means skip if absent.
TABLES: dict[str, dict] = {
    "t_edges": {"name": "transfer_edge", "required": False},
    "t_occupancy": {"name": "subscriber_occupancy", "required": False},
    "t_family": {"name": "ban_family", "required": False},
    "t_centrality": {"name": "ban_centrality", "required": False},
    "t_lineage": {"name": "wo_lineage", "required": False},
    "t_accounts": {"name": "src_account", "required": False},
}

# logical key -> physical column. Unused columns are ignored if absent.
COLS: dict[str, str] = {
    "account": "ban",
    "line": "sub_id",
    "from_account": "from_ban",
    "to_account": "to_ban",
    "transfer_dt": "transfer_dt",
    "start_dt": "start_dt",
    "end_dt": "end_dt",
    "family_id": "family_id",
    "family_size": "family_size",
    "in_degree": "in_degree",
    "out_degree": "out_degree",
    "wo_amt_current": "eq_wo_amt_current",
    "installment_id": "installment_id",
    "path_type": "path_type",
    "eq_type": "eq_type",
    "orig_dt": "orig_dt",
    "wo_dt": "wo_dt",
    "wo_amt": "wo_amt",
    "origin_account": "originated_ban",
    "chargeoff_account": "chargeoff_ban",
    "hop_count": "hop_count",
    "window": "window",
}

# =============================================================================
# Runtime — do not hardcode table/column names below this line
# =============================================================================

STYLESHEET = [
    {
        "selector": "node",
        "style": {
            "label": "data(label)",
            "width": "data(size)",
            "height": "data(size)",
            "font-size": 8,
            "text-valign": "center",
            "text-halign": "center",
            "color": "#111",
            "background-color": "data(color)",
            "border-width": 1,
            "border-color": "#222",
        },
    },
    {
        "selector": "edge",
        "style": {
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#555",
            "line-color": "#888",
            "width": "data(width)",
            "arrow-scale": 0.8,
        },
    },
    {
        "selector": ":selected",
        "style": {
            "border-width": 3,
            "border-color": "#1a5276",
            "line-color": "#1a5276",
            "target-arrow-color": "#1a5276",
        },
    },
]


def ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def esc(value: str) -> str:
    return str(value).replace("'", "''")


def t(logical: str) -> str:
    return ident(TABLES[logical]["name"])


def c(logical: str) -> str:
    return ident(COLS[logical])


def query_pl(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    return pl.from_arrow(con.execute(sql).arrow())


class Catalog:
    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self.tables: dict[str, set[str]] = {}
        raw = query_pl(
            con,
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema IN ('main', 'temp')
            """,
        )
        for row in raw.iter_rows(named=True):
            self.tables.setdefault(str(row["table_name"]).lower(), set()).add(
                str(row["column_name"]).lower()
            )
        self.present: dict[str, bool] = {}
        for logical, spec in TABLES.items():
            self.present[logical] = spec["name"].lower() in self.tables

    def has_table(self, logical: str) -> bool:
        return bool(self.present.get(logical))

    def has_col(self, logical_table: str, logical_col: str) -> bool:
        if not self.has_table(logical_table):
            return False
        physical_table = TABLES[logical_table]["name"].lower()
        physical_col = COLS[logical_col].lower()
        return physical_col in self.tables.get(physical_table, set())

    def refresh(self) -> None:
        self.__init__(self.con)

    def status_lines(self) -> list[str]:
        lines = []
        for logical, spec in TABLES.items():
            mark = "on" if self.has_table(logical) else "off"
            lines.append(f"{logical}={spec['name']} [{mark}]")
        return lines


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.size: dict[str, int] = {}

    def add(self, x: str) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.size[x] = 1

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def ensure_edge_table(con: duckdb.DuckDBPyConnection, cat: Catalog) -> None:
    """If transfer_edge is missing, derive it from occupancy consecutive stays."""
    if cat.has_table("t_edges"):
        return
    if not (
        cat.has_table("t_occupancy")
        and cat.has_col("t_occupancy", "line")
        and cat.has_col("t_occupancy", "account")
        and cat.has_col("t_occupancy", "start_dt")
        and cat.has_col("t_occupancy", "end_dt")
    ):
        return
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {t('t_edges')} AS
        SELECT
          a.{c('account')} AS {c('from_account')},
          b.{c('account')} AS {c('to_account')},
          a.{c('line')}    AS {c('line')},
          b.{c('start_dt')} AS {c('transfer_dt')}
        FROM {t('t_occupancy')} a
        JOIN {t('t_occupancy')} b
          ON a.{c('line')} = b.{c('line')}
         AND a.{c('end_dt')} = b.{c('start_dt')}
         AND a.{c('account')} <> b.{c('account')}
        """
    )
    TABLES["t_edges"]["name"] = TABLES["t_edges"]["name"]
    cat.refresh()
    cat.present["t_edges"] = True
    edge_name = TABLES["t_edges"]["name"].lower()
    cat.tables[edge_name] = {
        COLS["from_account"].lower(),
        COLS["to_account"].lower(),
        COLS["line"].lower(),
        COLS["transfer_dt"].lower(),
    }


def ensure_family_table(con: duckdb.DuckDBPyConnection, cat: Catalog) -> None:
    """If ban_family is missing, build WCC families from undirected transfer pairs."""
    if cat.has_table("t_family"):
        return
    if not cat.has_table("t_edges"):
        return
    pairs = query_pl(
        con,
        f"""
        SELECT DISTINCT
          CAST({c('from_account')} AS VARCHAR) AS a,
          CAST({c('to_account')} AS VARCHAR) AS b
        FROM {t('t_edges')}
        WHERE {c('from_account')} IS NOT NULL
          AND {c('to_account')} IS NOT NULL
          AND {c('from_account')} <> {c('to_account')}
        """,
    )
    uf = UnionFind()
    for row in pairs.iter_rows(named=True):
        uf.union(str(row["a"]), str(row["b"]))

    groups: dict[str, list[str]] = {}
    for node in uf.parent:
        groups.setdefault(uf.find(node), []).append(node)

    ranked = sorted(groups.values(), key=lambda xs: (-len(xs), min(xs)))
    rows = []
    for family_id, members in enumerate(ranked):
        size = len(members)
        for ban in members:
            rows.append(
                {
                    COLS["account"]: ban,
                    COLS["family_id"]: family_id,
                    COLS["family_size"]: size,
                }
            )

    if not rows:
        return

    tmp = pl.DataFrame(rows)
    con.register("_ban_family_src", tmp.to_arrow())
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {t('t_family')} AS
        SELECT * FROM _ban_family_src
        """
    )
    con.unregister("_ban_family_src")
    cat.refresh()
    cat.present["t_family"] = True
    fam_name = TABLES["t_family"]["name"].lower()
    cat.tables[fam_name] = {
        COLS["account"].lower(),
        COLS["family_id"].lower(),
        COLS["family_size"].lower(),
    }


def default_as_of(con: duckdb.DuckDBPyConnection, cat: Catalog) -> str:
    if cat.has_table("t_edges") and cat.has_col("t_edges", "transfer_dt"):
        df = query_pl(
            con,
            f"SELECT max({c('transfer_dt')}) AS d FROM {t('t_edges')}",
        )
        val = df["d"][0]
        if val is not None:
            return str(val)[:10]
    if cat.has_table("t_occupancy") and cat.has_col("t_occupancy", "start_dt"):
        df = query_pl(
            con,
            f"SELECT max({c('start_dt')}) AS d FROM {t('t_occupancy')}",
        )
        val = df["d"][0]
        if val is not None:
            return str(val)[:10]
    return date.today().isoformat()


def load_family_options(con: duckdb.DuckDBPyConnection, cat: Catalog) -> list[dict]:
    if not cat.has_table("t_family"):
        return []
    size_filter = ""
    if cat.has_col("t_family", "family_size"):
        size_filter = (
            f"WHERE {c('family_size')} BETWEEN 2 AND {int(APP['max_nodes'])}"
        )
    df = query_pl(
        con,
        f"""
        SELECT
          {c('family_id')} AS family_id,
          {"any_value(" + c("family_size") + ")" if cat.has_col("t_family", "family_size") else "count(*)"}
            AS family_size
        FROM {t('t_family')}
        {size_filter}
        GROUP BY {c('family_id')}
        ORDER BY family_size DESC
        LIMIT {int(APP['family_option_limit'])}
        """,
    )
    return [
        {
            "label": f"family {r['family_id']} | {r['family_size']} BANs",
            "value": int(r["family_id"]),
        }
        for r in df.iter_rows(named=True)
    ]


def family_elements(
    con: duckdb.DuckDBPyConnection,
    cat: Catalog,
    family_id: int,
    as_of: str | None,
    lookback_days: int | None,
) -> tuple[list[dict], str]:
    if not cat.has_table("t_family"):
        return [], "No family table and could not build one from edges."

    extra_cols = ""
    extra_join = ""
    if cat.has_table("t_centrality"):
        extra_join = (
            f"LEFT JOIN {t('t_centrality')} c "
            f"ON c.{c('account')} = f.{c('account')}"
        )
        if cat.has_col("t_centrality", "in_degree"):
            extra_cols += f", coalesce(c.{c('in_degree')}, 0) AS in_degree"
        if cat.has_col("t_centrality", "out_degree"):
            extra_cols += f", coalesce(c.{c('out_degree')}, 0) AS out_degree"
        if cat.has_col("t_centrality", "wo_amt_current"):
            extra_cols += f", coalesce(c.{c('wo_amt_current')}, 0) AS wo_amt"

    live_col = ", 0 AS live_subs"
    if (
        cat.has_table("t_occupancy")
        and as_of
        and cat.has_col("t_occupancy", "start_dt")
        and cat.has_col("t_occupancy", "end_dt")
    ):
        extra_join += f"""
        LEFT JOIN (
          SELECT o.{c('account')} AS account, count(*) AS live_subs
          FROM {t('t_occupancy')} o
          WHERE o.{c('start_dt')} <= DATE '{esc(as_of)}'
            AND o.{c('end_dt')}   >  DATE '{esc(as_of)}'
          GROUP BY 1
        ) s ON s.account = f.{c('account')}
        """
        live_col = ", coalesce(s.live_subs, 0) AS live_subs"

    nodes = query_pl(
        con,
        f"""
        SELECT
          f.{c('account')} AS account
          {extra_cols}
          {live_col}
        FROM {t('t_family')} f
        {extra_join}
        WHERE f.{c('family_id')} = {int(family_id)}
        """,
    )
    if "in_degree" not in nodes.columns:
        nodes = nodes.with_columns(pl.lit(0).alias("in_degree"))
    if "out_degree" not in nodes.columns:
        nodes = nodes.with_columns(pl.lit(0).alias("out_degree"))
    if "wo_amt" not in nodes.columns:
        nodes = nodes.with_columns(pl.lit(0.0).alias("wo_amt"))
    if "live_subs" not in nodes.columns:
        nodes = nodes.with_columns(pl.lit(0).alias("live_subs"))

    n_nodes = nodes.height
    if n_nodes == 0:
        return [], "Family has no BANs."
    if n_nodes > int(APP["max_nodes"]):
        return [], f"Family too large ({n_nodes} BANs). Raise APP['max_nodes']."

    date_filter = ""
    if (
        cat.has_table("t_edges")
        and cat.has_col("t_edges", "transfer_dt")
        and as_of
        and lookback_days
    ):
        window_start = (
            date.fromisoformat(as_of[:10]) - timedelta(days=int(lookback_days))
        ).isoformat()
        date_filter = (
            f"AND e.{c('transfer_dt')} >  DATE '{esc(window_start)}' "
            f"AND e.{c('transfer_dt')} <= DATE '{esc(as_of[:10])}'"
        )

    if cat.has_table("t_edges"):
        edges = query_pl(
            con,
            f"""
            SELECT
              e.{c('from_account')} AS from_account,
              e.{c('to_account')} AS to_account,
              count(*) AS n_subs
            FROM {t('t_edges')} e
            JOIN {t('t_family')} a ON a.{c('account')} = e.{c('from_account')}
            JOIN {t('t_family')} b ON b.{c('account')} = e.{c('to_account')}
            WHERE a.{c('family_id')} = {int(family_id)}
              AND b.{c('family_id')} = {int(family_id)}
              AND e.{c('from_account')} <> e.{c('to_account')}
              {date_filter}
            GROUP BY 1, 2
            """,
        )
    else:
        edges = pl.DataFrame(
            schema={"from_account": pl.Utf8, "to_account": pl.Utf8, "n_subs": pl.Int64}
        )

    wo_max = float(nodes["wo_amt"].max() or 0.0) or 1.0
    elements: list[dict] = []
    for r in nodes.iter_rows(named=True):
        wo = float(r["wo_amt"] or 0)
        acct = str(r["account"])
        elements.append(
            {
                "data": {
                    "id": acct,
                    "label": acct,
                    "account": acct,
                    "wo_amt": wo,
                    "live_subs": int(r["live_subs"] or 0),
                    "in_degree": int(r["in_degree"] or 0),
                    "out_degree": int(r["out_degree"] or 0),
                    "size": 18 + 42 * (wo / wo_max),
                    "color": "#c0392b" if wo > 0 else "#95a5a6",
                }
            }
        )

    node_ids = set(nodes["account"].cast(pl.Utf8).to_list())
    for r in edges.iter_rows(named=True):
        src, dst = str(r["from_account"]), str(r["to_account"])
        if src not in node_ids or dst not in node_ids:
            continue
        n_subs = int(r["n_subs"])
        elements.append(
            {
                "data": {
                    "id": f"{src}->{dst}",
                    "source": src,
                    "target": dst,
                    "n_subs": n_subs,
                    "width": min(1 + n_subs, 8),
                }
            }
        )

    meta = f"{n_nodes} BANs, {edges.height} directed pairs"
    return elements, meta


def occupancy_rows(
    con: duckdb.DuckDBPyConnection, cat: Catalog, account: str, as_of: str | None
) -> pl.DataFrame:
    if not (cat.has_table("t_occupancy") and as_of):
        return pl.DataFrame()
    where = [f"o.{c('account')} = '{esc(account)}'"]
    if cat.has_col("t_occupancy", "start_dt") and cat.has_col("t_occupancy", "end_dt"):
        where.append(
            f"o.{c('start_dt')} <= DATE '{esc(as_of)}' "
            f"AND o.{c('end_dt')} > DATE '{esc(as_of)}'"
        )
    select_bits = [f"o.{c('account')} AS ban"]
    if cat.has_col("t_occupancy", "line"):
        select_bits.insert(0, f"o.{c('line')} AS sub_id")
    if cat.has_col("t_occupancy", "start_dt"):
        select_bits.append(f"o.{c('start_dt')} AS start_dt")
    if cat.has_col("t_occupancy", "end_dt"):
        select_bits.append(f"o.{c('end_dt')} AS end_dt")
    return query_pl(
        con,
        f"""
        SELECT {", ".join(select_bits)}
        FROM {t('t_occupancy')} o
        WHERE {" AND ".join(where)}
        LIMIT {int(APP['occupancy_row_limit'])}
        """,
    )


def lineage_rows(
    con: duckdb.DuckDBPyConnection, cat: Catalog, account: str
) -> pl.DataFrame:
    if not cat.has_table("t_lineage"):
        return pl.DataFrame()
    wanted = [
        "installment_id",
        "line",
        "path_type",
        "eq_type",
        "orig_dt",
        "wo_dt",
        "wo_amt",
        "origin_account",
        "chargeoff_account",
        "hop_count",
        "window",
    ]
    select_bits = []
    for logical in wanted:
        if cat.has_col("t_lineage", logical):
            select_bits.append(f"{c(logical)} AS {logical}")
    if not select_bits:
        return pl.DataFrame()
    filters = []
    if cat.has_col("t_lineage", "chargeoff_account"):
        filters.append(f"{c('chargeoff_account')} = '{esc(account)}'")
    if cat.has_col("t_lineage", "origin_account"):
        filters.append(f"{c('origin_account')} = '{esc(account)}'")
    if not filters:
        return pl.DataFrame()
    order = f"ORDER BY {c('wo_amt')} DESC" if cat.has_col("t_lineage", "wo_amt") else ""
    return query_pl(
        con,
        f"""
        SELECT {", ".join(select_bits)}
        FROM {t('t_lineage')}
        WHERE {" OR ".join(filters)}
        {order}
        LIMIT {int(APP['lineage_row_limit'])}
        """,
    )


# --- boot (never raise on optional tables) ---

BOOT_ERROR = None
con: duckdb.DuckDBPyConnection | None = None
cat: Catalog | None = None
FAMILY_OPTIONS: list[dict] = []
DEFAULT_FAMILY = None
DEFAULT_AS_OF = date.today().isoformat()
HAS_TRANSFER_DT = False
HAS_OCCUPANCY = False
STATUS = "not started"

try:
    db_path = Path(APP["db_path"])
    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB file not found: {db_path.resolve()}")
    con = duckdb.connect(str(db_path), read_only=True)
    cat = Catalog(con)
    ensure_edge_table(con, cat)
    ensure_family_table(con, cat)
    FAMILY_OPTIONS = load_family_options(con, cat)
    DEFAULT_FAMILY = FAMILY_OPTIONS[0]["value"] if FAMILY_OPTIONS else None
    DEFAULT_AS_OF = default_as_of(con, cat)
    HAS_TRANSFER_DT = bool(cat and cat.has_table("t_edges") and cat.has_col("t_edges", "transfer_dt"))
    HAS_OCCUPANCY = bool(cat and cat.has_table("t_occupancy"))
    STATUS = " | ".join(cat.status_lines()) if cat else "no catalog"
except Exception as exc:  # noqa: BLE001
    BOOT_ERROR = f"{type(exc).__name__}: {exc}"
    STATUS = BOOT_ERROR

app = Dash(__name__)
app.title = "Transfer family explorer"

app.layout = html.Div(
    style={"fontFamily": "system-ui, sans-serif", "margin": "16px"},
    children=[
        html.H2("Transfer family explorer"),
        html.P(
            "Nodes are BANs. Arrows are sub_id moves. "
            "A sub_id can change BAN over time; it can only sit on one BAN on the as-of date."
        ),
        html.Pre(
            ("BOOT ERROR: " + BOOT_ERROR) if BOOT_ERROR else STATUS,
            style={"background": "#f4f4f4", "padding": "8px", "whiteSpace": "pre-wrap"},
        ),
        html.Div(
            style={"display": "grid", "gridTemplateColumns": "2fr 1fr 1fr", "gap": "12px"},
            children=[
                html.Div(
                    [
                        html.Label("Family"),
                        dcc.Dropdown(
                            id="family-dropdown",
                            options=FAMILY_OPTIONS,
                            value=DEFAULT_FAMILY,
                            clearable=False,
                        ),
                    ]
                ),
                html.Div(
                    [
                        html.Label("As-of date"),
                        dcc.DatePickerSingle(
                            id="as-of",
                            date=DEFAULT_AS_OF,
                            display_format="YYYY-MM-DD",
                            disabled=not (HAS_TRANSFER_DT or HAS_OCCUPANCY),
                        ),
                    ]
                ),
                html.Div(
                    [
                        html.Label("Transfer lookback (days)"),
                        dcc.Input(
                            id="lookback",
                            type="number",
                            value=APP["lookback_days"],
                            min=1,
                            step=1,
                            disabled=not HAS_TRANSFER_DT,
                            style={"width": "100%"},
                        ),
                    ]
                ),
            ],
        ),
        html.Div(id="graph-meta", style={"margin": "8px 0"}),
        cyto.Cytoscape(
            id="family-net",
            layout={"name": "cose", "animate": False},
            style={"width": "100%", "height": "600px", "border": "1px solid #ddd"},
            stylesheet=STYLESHEET,
            minZoom=0.15,
            maxZoom=3,
            elements=[],
        ),
        html.H3("Selected BAN"),
        html.Pre(id="node-detail", style={"background": "#f4f4f4", "padding": "8px"}),
        html.H4("Subs on this BAN as of date"),
        dash_table.DataTable(
            id="occupancy-table",
            page_size=8,
            style_table={"overflowX": "auto"},
            style_cell={"fontSize": 12, "padding": "4px"},
        ),
        html.H4("Installment lineage"),
        dash_table.DataTable(
            id="lineage-table",
            page_size=8,
            style_table={"overflowX": "auto"},
            style_cell={"fontSize": 12, "padding": "4px"},
        ),
    ],
)


@callback(
    Output("family-net", "elements"),
    Output("graph-meta", "children"),
    Input("family-dropdown", "value"),
    Input("as-of", "date"),
    Input("lookback", "value"),
)
def render_family(family_id, as_of, lookback):
    if BOOT_ERROR or con is None or cat is None:
        return [], BOOT_ERROR or "Database not connected."
    if family_id is None:
        return [], (
            "No families. Need transfer_edge (or occupancy to derive it). "
            "Set TABLES/COLS to your physical names."
        )
    as_of_s = str(as_of)[:10] if as_of else None
    elements, meta = family_elements(
        con, cat, int(family_id), as_of_s, int(lookback or APP["lookback_days"])
    )
    return elements, meta


@callback(
    Output("node-detail", "children"),
    Output("occupancy-table", "data"),
    Output("occupancy-table", "columns"),
    Output("lineage-table", "data"),
    Output("lineage-table", "columns"),
    Input("family-net", "tapNodeData"),
    Input("as-of", "date"),
)
def show_account(node_data, as_of):
    empty = ([], [])
    if not node_data or con is None or cat is None:
        return "Click a BAN.", [], [], [], []
    account = str(node_data.get("account", ""))
    as_of_s = str(as_of)[:10] if as_of else None
    detail = (
        f"BAN {account}\n"
        f"live subs: {node_data.get('live_subs')}\n"
        f"in-degree {node_data.get('in_degree')} | out-degree {node_data.get('out_degree')}\n"
        f"equipment WO ${float(node_data.get('wo_amt') or 0):,.2f}"
    )
    occ = occupancy_rows(con, cat, account, as_of_s)
    lin = lineage_rows(con, cat, account)
    occ_d, occ_c = (
        (occ.to_dicts(), [{"name": x, "id": x} for x in occ.columns])
        if not occ.is_empty()
        else empty
    )
    lin_d, lin_c = (
        (lin.to_dicts(), [{"name": x, "id": x} for x in lin.columns])
        if not lin.is_empty()
        else empty
    )
    return detail, occ_d, occ_c, lin_d, lin_c


if __name__ == "__main__":
    app.run(host=APP["host"], port=int(APP["port"]), debug=True)
