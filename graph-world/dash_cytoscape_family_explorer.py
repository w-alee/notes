"""
Local Dash-Cytoscape explorer for transfer families.

DuckDB -> Arrow -> Polars. No pandas.

Change every physical table/column name in SCHEMA below.
Logical names (account, line, family_id, ...) stay fixed in Python.

Run:
  python dash_cytoscape_family_explorer.py
  http://127.0.0.1:8050
"""

from __future__ import annotations

from pathlib import Path

import dash_cytoscape as cyto
import duckdb
import polars as pl
from dash import Dash, Input, Output, callback, dash_table, dcc, html

# =============================================================================
# 1. Connection
# =============================================================================

DB_PATH = Path("your_extract.duckdb")
HOST = "127.0.0.1"
PORT = 8050
MAX_NODES = 800
CURRENT_WINDOW = "current_12m"

# =============================================================================
# 2. SCHEMA — edit this block when your names differ
#
# Left side  = logical name the app uses
# Right side = physical name in DuckDB
#
# Example: if BAN is stored as account_id, set
#   "account": "account_id"
# =============================================================================

SCHEMA: dict[str, str] = {
    # tables
    "t_family": "ban_family",
    "t_edges": "transfer_edge",
    "t_centrality": "ban_centrality",
    "t_lineage": "wo_lineage",
    # account / family
    "account": "ban",
    "family_id": "family_id",
    "family_size": "family_size",
    # transfer edge
    "from_account": "from_ban",
    "to_account": "to_ban",
    "line": "ctn",
    # centrality (optional table)
    "in_degree": "in_degree",
    "out_degree": "out_degree",
    "wo_amt_current": "eq_wo_amt_current",
    # lineage (optional table)
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
# 3. Required physical tables and 3-row samples
#
# Rename columns in SCHEMA to match whatever you actually stored.
# Grain and meaning must stay the same.
# =============================================================================
#
# t_family  (required)  grain: one account
# -----------------------------------------
# | ban      | family_id | family_size |
# | BAN1001  | 0         | 4           |
# | BAN2044  | 0         | 4           |
# | BAN3309  | 1         | 1           |
#
# If your file uses account_id / component_id / n_accounts, set:
#   "account": "account_id"
#   "family_id": "component_id"
#   "family_size": "n_accounts"
#   "t_family": "account_family"
#
# t_edges  (required)  grain: one line movement
# ---------------------------------------------
# | from_ban | to_ban  | ctn        |
# | BAN1001  | BAN2044 | 2145550101 |
# | BAN1001  | BAN5520 | 4695550144 |
# | BAN0882  | BAN2044 | 2145550188 |
#
# If you use account / subscriber:
#   "from_account": "from_account"
#   "to_account": "to_account"
#   "line": "subscriber_id"
#
# t_centrality  (optional)  grain: one account
# --------------------------------------------
# | ban     | in_degree | out_degree | eq_wo_amt_current |
# | BAN2044 | 2         | 0          | 1268.14           |
# | BAN1001 | 0         | 2          | 0.00              |
# | BAN0882 | 0         | 1          | 0.00              |
#
# Missing table is fine. Degrees and WO size fall back to 0 / grey nodes.
#
# t_lineage  (optional)  grain: one written-off installment
# ---------------------------------------------------------
# | installment_id | ctn        | path_type               | eq_type | orig_dt    | wo_dt       | wo_amt | originated_ban | chargeoff_ban | hop_count | window      |
# | EIP88101       | 2145550101 | moved_eip               | PHONE   | 2024-12-20 | 2025-11-02  | 870.14 | BAN1001        | BAN2044       | 1         | current_12m |
# | EIP88102       | 2145550101 | new_eip_after_transfer  | WATCH   | 2025-04-01 | 2025-11-02  | 398.00 | BAN2044        | BAN2044       | 1         | current_12m |
# | EIP77011       | 5125550199 | singleton               | PHONE   | 2023-06-01 | 2025-08-14  | 210.00 | BAN3309        | BAN3309       | 0         | prior_12m   |
#
# window values the app filters on: current_12m / prior_12m
# path_type values from Phase 3: moved_eip, new_eip_after_transfer,
#   new_eip_on_transfer_family, singleton, ambiguous
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


def q(key: str) -> str:
    """Quoted DuckDB identifier from SCHEMA."""
    name = SCHEMA[key]
    return '"' + name.replace('"', '""') + '"'


def sql_escape(value: str) -> str:
    return value.replace("'", "''")


def query_pl(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    return pl.from_arrow(con.execute(sql).arrow())


def table_exists(con: duckdb.DuckDBPyConnection, logical_table: str) -> bool:
    physical = SCHEMA[logical_table]
    df = query_pl(
        con,
        f"""
        SELECT 1 AS ok
        FROM information_schema.tables
        WHERE lower(table_name) = lower('{sql_escape(physical)}')
        LIMIT 1
        """,
    )
    return df.height > 0


def open_db() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DuckDB file not found: {DB_PATH.resolve()}")
    return duckdb.connect(str(DB_PATH), read_only=True)


def load_family_options(con: duckdb.DuckDBPyConnection) -> list[dict]:
    if table_exists(con, "t_lineage"):
        wo_expr = (
            f"coalesce(sum(l.{q('wo_amt')}) "
            f"FILTER (WHERE l.{q('window')} = '{sql_escape(CURRENT_WINDOW)}'), 0)"
        )
        join_sql = (
            f"LEFT JOIN {q('t_lineage')} l "
            f"ON l.{q('chargeoff_account')} = f.{q('account')}"
        )
    else:
        wo_expr = "0"
        join_sql = ""

    df = query_pl(
        con,
        f"""
        SELECT
          f.{q('family_id')} AS family_id,
          any_value(f.{q('family_size')}) AS family_size,
          {wo_expr} AS wo_current
        FROM {q('t_family')} f
        {join_sql}
        WHERE f.{q('family_size')} BETWEEN 2 AND {int(MAX_NODES)}
        GROUP BY f.{q('family_id')}
        ORDER BY wo_current DESC, family_size DESC
        LIMIT 300
        """,
    )
    return [
        {
            "label": (
                f"family {r['family_id']} | size {r['family_size']} "
                f"| WO ${r['wo_current']:,.0f}"
            ),
            "value": int(r["family_id"]),
        }
        for r in df.iter_rows(named=True)
    ]


def family_elements(
    con: duckdb.DuckDBPyConnection, family_id: int
) -> tuple[list[dict], int, int]:
    if table_exists(con, "t_centrality"):
        deg_join = (
            f"LEFT JOIN {q('t_centrality')} c "
            f"ON c.{q('account')} = f.{q('account')}"
        )
        in_expr = f"coalesce(c.{q('in_degree')}, 0)"
        out_expr = f"coalesce(c.{q('out_degree')}, 0)"
        wo_expr = f"coalesce(c.{q('wo_amt_current')}, 0)"
    else:
        deg_join = ""
        in_expr = "0"
        out_expr = "0"
        wo_expr = "0"

    nodes = query_pl(
        con,
        f"""
        SELECT
          f.{q('account')} AS account,
          f.{q('family_size')} AS family_size,
          {in_expr} AS in_degree,
          {out_expr} AS out_degree,
          {wo_expr} AS wo_amt
        FROM {q('t_family')} f
        {deg_join}
        WHERE f.{q('family_id')} = {int(family_id)}
        """,
    )
    edges = query_pl(
        con,
        f"""
        SELECT
          e.{q('from_account')} AS from_account,
          e.{q('to_account')} AS to_account,
          count(*) AS n_lines
        FROM {q('t_edges')} e
        JOIN {q('t_family')} a ON a.{q('account')} = e.{q('from_account')}
        JOIN {q('t_family')} b ON b.{q('account')} = e.{q('to_account')}
        WHERE a.{q('family_id')} = {int(family_id)}
          AND b.{q('family_id')} = {int(family_id)}
          AND e.{q('from_account')} <> e.{q('to_account')}
        GROUP BY 1, 2
        """,
    )

    n_nodes = nodes.height
    if n_nodes == 0:
        return [], 0, 0
    if n_nodes > MAX_NODES:
        return [], n_nodes, edges.height

    wo_max = float(nodes["wo_amt"].max() or 0.0)
    wo_max = wo_max if wo_max > 0 else 1.0

    elements: list[dict] = []
    for r in nodes.iter_rows(named=True):
        wo = float(r["wo_amt"])
        acct = str(r["account"])
        elements.append(
            {
                "data": {
                    "id": acct,
                    "label": acct,
                    "account": acct,
                    "wo_amt": wo,
                    "in_degree": int(r["in_degree"]),
                    "out_degree": int(r["out_degree"]),
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
        n_lines = int(r["n_lines"])
        elements.append(
            {
                "data": {
                    "id": f"{src}->{dst}",
                    "source": src,
                    "target": dst,
                    "n_lines": n_lines,
                    "width": min(1 + n_lines, 8),
                }
            }
        )

    return elements, n_nodes, edges.height


def account_lineage(con: duckdb.DuckDBPyConnection, account: str) -> pl.DataFrame:
    if not table_exists(con, "t_lineage"):
        return pl.DataFrame()
    acct = sql_escape(account)
    return query_pl(
        con,
        f"""
        SELECT
          {q('installment_id')} AS installment_id,
          {q('line')} AS line,
          {q('path_type')} AS path_type,
          {q('eq_type')} AS eq_type,
          {q('orig_dt')} AS orig_dt,
          {q('wo_dt')} AS wo_dt,
          {q('wo_amt')} AS wo_amt,
          {q('origin_account')} AS origin_account,
          {q('chargeoff_account')} AS chargeoff_account,
          {q('hop_count')} AS hop_count,
          {q('window')} AS window
        FROM {q('t_lineage')}
        WHERE {q('chargeoff_account')} = '{acct}'
           OR {q('origin_account')} = '{acct}'
        ORDER BY {q('wo_amt')} DESC
        LIMIT 50
        """,
    )


con = open_db()
FAMILY_OPTIONS = load_family_options(con)
DEFAULT_FAMILY = FAMILY_OPTIONS[0]["value"] if FAMILY_OPTIONS else None

app = Dash(__name__)
app.title = "Transfer family explorer"

app.layout = html.Div(
    style={"fontFamily": "system-ui, sans-serif", "margin": "16px"},
    children=[
        html.H2("Transfer family explorer"),
        html.P(
            "Select a connected-account family. Red nodes have current-window "
            "equipment write-offs. Arrows are directed line transfers. "
            "Click a node for installment lineage."
        ),
        html.Div(
            style={"display": "flex", "gap": "16px", "alignItems": "center"},
            children=[
                html.Div(
                    style={"flex": "1"},
                    children=[
                        html.Label("Family"),
                        dcc.Dropdown(
                            id="family-dropdown",
                            options=FAMILY_OPTIONS,
                            value=DEFAULT_FAMILY,
                            clearable=False,
                        ),
                    ],
                ),
                html.Div(id="graph-meta", style={"minWidth": "240px"}),
            ],
        ),
        cyto.Cytoscape(
            id="family-net",
            layout={"name": "cose", "animate": False},
            style={"width": "100%", "height": "620px", "border": "1px solid #ddd"},
            stylesheet=STYLESHEET,
            minZoom=0.15,
            maxZoom=3,
            elements=[],
        ),
        html.H3("Selected account"),
        html.Pre(id="node-detail", style={"background": "#f4f4f4", "padding": "8px"}),
        dash_table.DataTable(
            id="lineage-table",
            page_size=10,
            style_table={"overflowX": "auto"},
            style_cell={"fontSize": 12, "padding": "4px"},
        ),
    ],
)


@callback(
    Output("family-net", "elements"),
    Output("graph-meta", "children"),
    Input("family-dropdown", "value"),
)
def render_family(family_id):
    if family_id is None:
        return [], (
            f"No families found. Need tables {SCHEMA['t_family']} "
            f"and {SCHEMA['t_edges']}."
        )
    elements, n_nodes, n_edges = family_elements(con, int(family_id))
    if n_nodes > MAX_NODES:
        return [], f"Family too large ({n_nodes} accounts). Cap is {MAX_NODES}."
    return elements, f"{n_nodes} accounts, {n_edges} directed pairs"


@callback(
    Output("node-detail", "children"),
    Output("lineage-table", "data"),
    Output("lineage-table", "columns"),
    Input("family-net", "tapNodeData"),
)
def show_account(node_data):
    if not node_data:
        return "Click a node.", [], []
    account = str(node_data.get("account", ""))
    detail = (
        f"account {account}\n"
        f"in-degree {node_data.get('in_degree')}\n"
        f"out-degree {node_data.get('out_degree')}\n"
        f"current equipment WO ${float(node_data.get('wo_amt') or 0):,.2f}"
    )
    lin = account_lineage(con, account)
    if lin.is_empty():
        return detail, [], []
    return detail, lin.to_dicts(), [{"name": c, "id": c} for c in lin.columns]


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=True)
