I’ll pull the Phase 2 walkthrough so the ban_family steps match the pipeline you already have.ban_family is not a source table. You compute it after you have transfer edges.
What it is
One row per BAN. family_id is the weakly connected component of the BAN–BAN transfer graph.
	•	Nodes = BANs
	•	An undirected edge exists if any sub_id ever moved between those two BANs
	•	Direction is ignored here
	•	BANs that never sent or received a sub_id get their own family of size 1
So if:
BAN1001 --sub 2145550101--> BAN2044
BAN1001 --sub 4695550144--> BAN5520
BAN0882 --sub 2145550188--> BAN2044
BAN3309  (no transfers)
then BAN1001, BAN2044, BAN5520, BAN0882 share family_id = 0 (family_size = 4). BAN3309 is family_id = 1 (family_size = 1).
That is why the explorer dropdown can say “family 0 | 4 BANs”.
Inputs you need first
transfer_edge (from occupancy: consecutive stays of the same sub_id where BAN changed)
from_ban
to_ban
sub_id
transfer_dt
BAN1001
BAN2044
2145550101
2025-03-09
BAN1001
BAN5520
4695550144
2025-01-12
BAN0882
BAN2044
2145550188
2025-06-20
ban_map — every BAN you care about, dense integer index 0 .. N-1
Include BANs that never transferred (from your account list). Otherwise isolates never get a family row.
Build it (rustworkx)
import duckdb
import polars as pl
import rustworkx as rx

con = duckdb.connect("your_extract.duckdb")

def qpl(sql: str) -> pl.DataFrame:
    return pl.from_arrow(con.execute(sql).arrow())

ban_map = qpl("SELECT ban, ban_idx FROM ban_map")
edges = qpl("""
    SELECT DISTINCT
      a.ban_idx AS src,
      b.ban_idx AS dst
    FROM transfer_edge e
    JOIN ban_map a ON a.ban = e.from_ban
    JOIN ban_map b ON b.ban = e.to_ban
    WHERE e.from_ban <> e.to_ban
""")

g = rx.PyGraph()
g.add_nodes_from(range(ban_map.height))
g.add_edges_from([(int(s), int(d), None) for s, d in edges.iter_rows()])

# one list of node indices per component
components = rx.connected_components(g)

rows = []
for family_id, nodes in enumerate(sorted(components, key=lambda c: (-len(c), min(c)))):
    size = len(nodes)
    for ban_idx in nodes:
        rows.append(
            {
                "ban_idx": int(ban_idx),
                "family_id": int(family_id),
                "family_size": int(size),
                "is_transfer_family": size >= 2,
            }
        )

family = pl.DataFrame(rows).join(ban_map, on="ban_idx")
con.register("ban_family_tmp", family.to_arrow())
con.execute("CREATE OR REPLACE TABLE ban_family AS SELECT * FROM ban_family_tmp")
Result grain: one row per BAN.
ban
ban_idx
family_id
family_size
is_transfer_family
BAN0882
0
0
4
true
BAN1001
1
0
4
true
BAN2044
2
0
4
true
BAN3309
3
1
1
false
BAN5520
4
0
4
true
family_id is just a label. What matters is which BANs share it.
If `ban_map` does not exist yet
CREATE OR REPLACE TABLE ban_map AS
SELECT ban, row_number() OVER (ORDER BY ban) - 1 AS ban_idx
FROM (
  SELECT ban FROM src_account
  UNION
  SELECT from_ban FROM transfer_edge
  UNION
  SELECT to_ban   FROM transfer_edge
);
Checks before you trust it
-- one row per BAN
SELECT count(*) AS n, count(DISTINCT ban) AS n_ban FROM ban_family;

-- no BAN in two families
SELECT ban, count(*) FROM ban_family GROUP BY 1 HAVING count(*) > 1;

-- family_size matches membership
SELECT family_id, any_value(family_size) AS stored, count(*) AS actual
FROM ban_family
GROUP BY 1
HAVING stored <> actual;

-- largest families (these are what the explorer should list first)
SELECT family_id, family_size
FROM ban_family
GROUP BY 1, 2
ORDER BY family_size DESC
LIMIT 20;
What this is not
	•	Not Leiden / communities (that is later, and can add address edges)
	•	Not “BANs that share a sub_id on the same date” (that must be zero)
	•	Not a list of sub_ids — those stay on transfer_edge and occupancy
Once ban_family exists, the explorer only needs: same family_id → draw those BANs and the transfer arrows among them.
