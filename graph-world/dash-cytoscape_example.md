I’ll add sample tables and a single config block so table and column names can be changed without editing query logic.The explorer now has one `SCHEMA` map and explicit 3-row samples. Change physical names there only. Do not edit the SQL.

## What you change

At the top of the file:

```python
SCHEMA: dict[str, str] = {
    "t_family": "ban_family",
    "t_edges": "transfer_edge",
    "account": "ban",
    "from_account": "from_ban",
    "to_account": "to_ban",
    "line": "ctn",
    ...
}
```

Left key = logical name the app uses. Right value = column or table name in DuckDB.

If BAN is `account_id` and CTN is `subscriber_id`:

```python
"t_family": "account_family",
"account": "account_id",
"from_account": "from_account",
"to_account": "to_account",
"line": "subscriber_id",
```

Python still talks about `account` and `line`. DuckDB is queried with your names.

## Tables the app expects

**Required — `t_family` (default `ban_family`)**  
One row per account.

| ban | family_id | family_size |
|---|---|---|
| BAN1001 | 0 | 4 |
| BAN2044 | 0 | 4 |
| BAN3309 | 1 | 1 |

BAN1001 and BAN2044 are in the same family. BAN3309 is a singleton and will not appear in the dropdown (`family_size` must be 2–800).

**Required — `t_edges` (default `transfer_edge`)**  
One row per line move.

| from_ban | to_ban | ctn |
|---|---|---|
| BAN1001 | BAN2044 | 2145550101 |
| BAN1001 | BAN5520 | 4695550144 |
| BAN0882 | BAN2044 | 2145550188 |

Those three edges are what the canvas draws as arrows.

**Optional — `t_centrality` (default `ban_centrality`)**

| ban | in_degree | out_degree | eq_wo_amt_current |
|---|---|---|---|
| BAN2044 | 2 | 0 | 1268.14 |
| BAN1001 | 0 | 2 | 0.00 |
| BAN0882 | 0 | 1 | 0.00 |

BAN2044 is red and larger because of write-off dollars. Missing table → all nodes grey, degrees 0.

**Optional — `t_lineage` (default `wo_lineage`)**

| installment_id | ctn | path_type | eq_type | orig_dt | wo_dt | wo_amt | originated_ban | chargeoff_ban | hop_count | window |
|---|---|---|---|---|---|---|---|---|---|---|
| EIP88101 | 2145550101 | moved_eip | PHONE | 2024-12-20 | 2025-11-02 | 870.14 | BAN1001 | BAN2044 | 1 | current_12m |
| EIP88102 | 2145550101 | new_eip_after_transfer | WATCH | 2025-04-01 | 2025-11-02 | 398.00 | BAN2044 | BAN2044 | 1 | current_12m |
| EIP77011 | 5125550199 | singleton | PHONE | 2023-06-01 | 2025-08-14 | 210.00 | BAN3309 | BAN3309 | 0 | prior_12m |

Click BAN2044 → first two rows. `window = current_12m` is what sizes/colors nodes and ranks the family dropdown.

## Still Polars-only

DuckDB results go through Arrow into Polars. The click table uses `to_dicts()`. There is no pandas import.