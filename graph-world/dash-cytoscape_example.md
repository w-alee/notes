# Transfer family explorer — instructions

Nodes are **BANs**. Edges are **sub_id moves**. Time is an **as-of date**.

A `sub_id` can sit on many BANs across history. On any one date it sits on exactly one BAN.

Stack: DuckDB → Arrow → Polars → Dash Cytoscape. No pandas.

---

## 1. Install and run

```bash
pip install dash dash-cytoscape duckdb polars pyarrow
```

In `dash_cytoscape_family_explorer.py` set `DB_PATH` to your `.duckdb` file.

```bash
python dash_cytoscape_family_explorer.py
```

Open `http://127.0.0.1:8050`.

---

## 2. What the picture means

| Shape | Meaning |
|---|---|
| Node | One BAN (account) |
| Arrow A → B | At least one `sub_id` transferred from BAN A to BAN B in the lookback window ending on the as-of date |
| Red / larger node | That BAN has current-window equipment write-off dollars |
| Click a node | `sub_id`s **on that BAN as of the selected date**, plus installment lineage |

Controls:

- **Family** — Phase 2 WCC id (`family_size` 2–800)
- **As-of date** — occupancy snapshot; each `sub_id` must have one BAN
- **Lookback (days)** — only draw transfers with `window_start < transfer_dt <= as_of`

---

## 3. Naming

Edit only the `SCHEMA` dict. Left = logical name. Right = DuckDB name.

Defaults:

| Logical | Physical default | Role |
|---|---|---|
| `account` | `ban` | Account / BAN |
| `line` | `sub_id` | Subscriber |
| `from_account` / `to_account` | `from_ban` / `to_ban` | Transfer endpoints |
| `start_dt` / `end_dt` | `start_dt` / `end_dt` | Occupancy interval |
| `transfer_dt` | `transfer_dt` | Date the sub changed BAN |
| `t_occupancy` | `subscriber_occupancy` | Time dimension |
| `t_family` | `ban_family` | Family membership |
| `t_edges` | `transfer_edge` | Directed moves |

If your subscriber column is still `ctn`, set `"line": "ctn"`. Do not change the rest of the file.

---

## 4. Tables and samples

Intervals are half-open: `start_dt <= as_of < end_dt`. Current stays use `end_dt = 9999-12-31`.

### `subscriber_occupancy` (required for time)

Grain: one stay of one `sub_id` on one BAN.

| sub_id | ban | start_dt | end_dt |
|---|---|---|---|
| 2145550101 | BAN1001 | 2022-01-15 | 2025-03-09 |
| 2145550101 | BAN2044 | 2025-03-09 | 9999-12-31 |
| 5125550199 | BAN3309 | 2023-06-01 | 9999-12-31 |

Same `sub_id` on two BANs **over time**. On 2025-01-01 it is only on BAN1001. On 2025-06-01 it is only on BAN2044.

### `transfer_edge` (required)

Grain: one `sub_id` movement.

| from_ban | to_ban | sub_id | transfer_dt |
|---|---|---|---|
| BAN1001 | BAN2044 | 2145550101 | 2025-03-09 |
| BAN1001 | BAN5520 | 4695550144 | 2025-01-12 |
| BAN0882 | BAN2044 | 2145550188 | 2025-06-20 |

`transfer_dt` should equal the destination occupancy `start_dt`.

### `ban_family` (required)

Grain: one BAN.

| ban | family_id | family_size |
|---|---|---|
| BAN1001 | 0 | 4 |
| BAN2044 | 0 | 4 |
| BAN3309 | 1 | 1 |

Family 0 is drawable. Family 1 is a singleton and is omitted from the dropdown.

### Optional

`ban_centrality` — `ban`, `in_degree`, `out_degree`, `eq_wo_amt_current`  
`wo_lineage` — installment path rows keyed by `originated_ban` / `chargeoff_ban` / `sub_id`

---

## 5. Time rule (do not violate)

```sql
-- must return 0 rows for a valid as-of date
SELECT sub_id
FROM subscriber_occupancy
WHERE start_dt <= DATE '2025-06-01'
  AND end_dt   >  DATE '2025-06-01'
GROUP BY sub_id
HAVING count(DISTINCT ban) > 1;
```

The UI prints that count. If it is not 0, occupancy intervals overlap and the snapshot is unsafe.

Build occupancy from Phase 1. Derive `transfer_edge` from consecutive stays of the same `sub_id` where `ban` changes.

---

## 6. How to read one example

`sub_id` 2145550101:

1. On BAN1001 from 2022-01-15 to 2025-03-09  
2. Moves to BAN2044 on 2025-03-09  
3. As-of 2025-06-01, lookback 365 days → one arrow BAN1001 → BAN2044  
4. Click BAN2044 → occupancy table shows 2145550101 live on that date  

That is the time dimension. The family graph is still BAN–BAN; `sub_id` is the payload on the edge and on the occupancy table.
