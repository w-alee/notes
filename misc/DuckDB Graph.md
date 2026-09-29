**Short answer:** Treat DuckDB as the system of record and DuckPGQ as a *view layer*, not a graph database. A local AT&T Mobility graph that stays dynamic *and* fast is feasible **if** you model BAN → CTN as relational grain, put raw events in partitioned Parquet/DuckLake, and only project *aggregated* edges into SQL/PGQ. A single mega-graph over raw CDR, bills, treatments, and marketing touches is **not** feasible on a laptop and is not what DuckPGQ is good at.

This is an architecture recommendation, not an app.

---

## Feasibility (current tech, August 2026)

| Layer | Verdict | Why |
|---|---|---|
| Relational OLAP in DuckDB (bills, status, monthly features) | **Strong / production-ready** | Columnar, out-of-core, Parquet, ART indexes, DuckLake 1.0 (Apr 2026). |
| SQL/PGQ pattern matching (DuckPGQ) | **Useful research tool, not the spine** | Official docs: community extension, still WIP, **not on DuckDB 1.5.x** — pin **v1.4.4**. Algorithms can fail (`csr_cte does not exist`). |
| Graph *algorithms* (Louvain, betweenness, PageRank, influence) | **Use Onager, not DuckPGQ** | DuckPGQ has 3 algos. Onager has 40+ as table functions on edge lists. |
| Variable-length paths / money-mule / household cycles | **Feasible on *summarized* graphs** | FinBench-style PGQ works. Unbounded hops on dense call graphs will explode. |
| National raw CDR as edges | **Not feasible locally** | Telco papers: ~16M nodes / 300M edges for *one* operator slice of *calls only*. AT&T-scale CDR is tens of billions of events. |
| Your 14" M4 Max / 64 GB | **Excellent for a *portfolio mart*** | Hundreds of millions of rows, or low billions of *narrow* aggregated facts. Not the warehouse. |
| Recursive CTEs | **The production fallback** | Native, no extension lag. DuckDB 2.0 (fall 2026) rewrote recursive CTEs (~40× on a 1M-edge reachability microbench) and added `USING KEY`. |

**Bottom line:** DuckPGQ is the right *language* for “who owns whom / who paid whom / who called whom last 90 days.” It is the wrong *storage engine* and the wrong place to put every function’s event stream.

The official DuckDB fraud post is the closest analog to your world: `Person → owns → Account → Transfer → Account`. That is exactly BAN/CTN + payment/treatment/device, **not** a knowledge graph of every transaction.

---

## Design principle

```
Events (immutable, partitioned Parquet / DuckLake)
        ↓
Identity + SCD bridges (BAN, CTN, device, SIM, party)
        ↓
Monthly marts + feature store (account grain + subscriber grain)
        ↓
Several small PROPERTY GRAPH views over those tables
        ↓
SQL/PGQ (patterns)  +  Onager (algorithms)  +  recursive CTE (critical paths)
```

DuckPGQ does **not** persist a graph. `CREATE PROPERTY GRAPH` is a schema overlay: vertices = tables, edges = tables with `SOURCE KEY` / `DESTINATION KEY`. If the tables are wrong, the graph is slow. If the tables are right, PGQ is just nicer SQL.

So “maximal performance” is 80% table design and 20% graph syntax.

---

## Grain (use AT&T names, not generic CRM)

Micro grain you described is correct, and it maps to AT&T eBonding language:

| Concept | AT&T-ish key | Role |
|---|---|---|
| Party / customer | `party_id` | Human or business. Can own many BANs. |
| Account | **BAN** | Bills, credit class, collections, involuntary churn. |
| Subscriber / line | **CTN** | Usage, device, rate plan, voluntary churn, port-out. |
| Device | IMEI | Fraud, upgrade, installment. |
| SIM | ICCID / IMSI | SIM-swap, identity. |
| Product | rate plan + SOCs | Sales, marketing, ARPU. |

**Cardinality:** 1 BAN → 1..N CTNs. 1 CTN → 0..1 current IMEI, 0..1 current SIM. Household / FAN sits *above* BAN.

**Critical rule:** Churn, collections, and billing live at **BAN**. Usage, device, and social influence live at **CTN**. If you collapse them, involuntary churn models and household graphs both break.

Do **not** make “transaction” a vertex. Transactions are **typed edges or fact tables**. FinBench made the same choice: Account is a vertex; Transfer is an edge with amount/time properties.

---

## Physical layout (what actually stays fast)

### 1. Lake (append-only, Hive-partitioned Parquet)

```
data/raw/
  usage/year=2026/month=08/*.parquet
  billing/year=2026/month=08/*.parquet
  payments/...
  treatments/...
  care/...
  marketing/...
  orders/...
  status_events/...
```

DuckDB guidance that still holds in 2026:

- File size **100 MB–10 GB**
- Row groups **100K–1M rows** (DuckDB parallelizes on row groups)
- At least as many row groups as threads
- Write Parquet *with DuckDB* so column stats exist (predicate pushdown)
- Partition by **month + domain**, not by BAN (over-partitioning kills you)

### 2. Catalog / mart (DuckLake or a `.duckdb` file)

For a local “dynamic” warehouse, **DuckLake 1.0** is the current best practice: Parquet on disk + SQL catalog (snapshots, time travel, schema). A single compressed `.duckdb` is simpler if the whole mart fits on the Mac.

Settings that matter:

```sql
SET preserve_insertion_order = false;
SET temp_directory = '/fast-ssd/duckdb_tmp';
-- threads: physical cores, not hyperthreads, unless I/O bound
```

Persistent compressed DuckDB often beats a giant uncompressed in-memory DB. Reuse one connection so the buffer cache stays warm.

### 3. Types (this is free performance)

- Keys: `BIGINT` (hash BAN/CTN if they are strings; keep natural keys alongside)
- Money: `DECIMAL(18,4)` or integer cents
- Codes: `ENUM` or dictionary-friendly `VARCHAR` (credit class, treatment, disconnect reason)
- Time: `DATE` on marts, `TIMESTAMPTZ` on events
- Flags: `BOOLEAN`, not `'Y'/'N'`

Sort/insert fact tables by `(month, account_sk)` or `(month, subscriber_sk)` so joins and zone maps hit.

---

## Logical schema (minimum that covers every function)

### Identity (slowly changing)

- `dim_party`
- `dim_account` — BAN, credit class, tenure, market, tenure start
- `dim_subscriber` — CTN, BAN_sk, activation, current status
- `dim_device`, `dim_sim`, `dim_product`
- `br_account_subscriber` — `valid_from`, `valid_to`, `is_current` (lines move)
- `br_subscriber_device`, `br_subscriber_sim` — same SCD2 pattern
- `br_household` — shared SSN/address/payment instrument (fraud + family plans)

### Events (never overwrite)

- `fact_bill` — BAN, bill_dt, ARPU, past_due, cycle
- `fact_payment` — BAN, amount, method, result
- `fact_treatment` — BAN, treatment_cd, agency, start/end (your collections stack)
- `fact_status` — BAN *and* CTN grains: voluntary / involuntary / port-out / suspend
- `fact_order` — sales, upgrade, addon
- `fact_care` — tickets, reason
- `fact_mkt_touch` — campaign, channel, response
- `fact_usage_daily` — **CTN-level aggregates** (MOU, MB, SMS, roaming). Not CDR.
- `fact_call_pair_month` — `(src_subscriber_sk, dst_subscriber_sk, month, calls, mou)`  
  This is the *only* social graph you should materialize locally.

### Feature store (what models actually consume)

- `fs_account_month` — RFM-ish billing, delinquency, treatment depth, tenure, n_lines, IVC/VC flags
- `fs_subscriber_month` — usage deltas, device age, SOC mix, social centrality, influencer flags

Rebuild these monthly (or nightly for a slice). Graph queries **write into** these tables; they are not the serving path for LightGBM.

---

## Graph layer: several small graphs, not one

One property graph with every edge type becomes a supernode mess (towers, payment processors, “AT&T care”, popular numbers). Define **domain graphs** over the same tables.

### G1 — Identity (always)

```sql
CREATE PROPERTY GRAPH identity
VERTEX TABLES (dim_party, dim_account, dim_subscriber, dim_device, dim_sim)
EDGE TABLES (
  br_account_subscriber
    SOURCE KEY (account_sk) REFERENCES dim_account (account_sk)
    DESTINATION KEY (subscriber_sk) REFERENCES dim_subscriber (subscriber_sk)
    LABEL has_line,
  br_subscriber_device
    SOURCE KEY (subscriber_sk) REFERENCES dim_subscriber (subscriber_sk)
    DESTINATION KEY (device_sk) REFERENCES dim_device (device_sk)
    LABEL uses_device,
  br_subscriber_sim
    SOURCE KEY (subscriber_sk) REFERENCES dim_subscriber (subscriber_sk)
    DESTINATION KEY (sim_sk) REFERENCES dim_sim (sim_sk)
    LABEL uses_sim
);
```

Use for: multi-line households, device reuse (fraud), SIM-swap, “all CTNs on a BAN.”

### G2 — Collections / billing (FinBench pattern)

Vertices: `dim_party`, `dim_account`.  
Edges: `owns`, `paid` (from `fact_payment` rolled to account-account or party-account), `treated` (treatment transitions), `same_instrument`.

This is the graph DuckPGQ is *proven* on: cycles, smurfing-like split payments, shared ownership.

```sql
FROM GRAPH_TABLE (collections
  MATCH p = ANY SHORTEST
    (a1:dim_account)-[t:paid]->+ (a2:dim_account)
  WHERE a1.account_sk <> a2.account_sk
  COLUMNS (path_length(p), a1.account_sk, a2.account_sk)
);
```

Cap hops in practice (`{1,4}`). Unbounded `*` on payment graphs is how you lock the laptop.

### G3 — Social / retention (aggregated only)

Vertices: `dim_subscriber`.  
Edge: `fact_call_pair_month` labeled `called`, properties `calls`, `mou`, `month`.

Onager, not PGQ, for the hard part:

```sql
SELECT node_id, community
FROM onager_cmm_louvain(
  (SELECT src_subscriber_sk, dst_subscriber_sk
   FROM fact_call_pair_month
   WHERE month >= DATE '2026-05-01' AND mou >= 5)
);
```

Then join communities onto `fs_subscriber_month` for influence / viral churn — the 2008–2020 telco SNA literature still holds: neighbor churn is a first-class feature.

Filter the edge list. A subscriber who called 8,000 numbers last month is a supernode (business line / telemarketer). Degree-cap or split B2B vs consumer.

### G4 — Fraud / identity theft

Vertices: CTN, IMEI, SIM, address, payment token.  
Edges: `used`, `shipped_to`, `paid_with`, `ported`.

Short pattern queries (same IMEI on many BANs in 7 days). Do **not** run global betweenness on this graph.

---

## How each AT&T function maps

| Function | Grain | Primary store | Graph? |
|---|---|---|---|
| Billing / ARPU | BAN × cycle | `fact_bill` | Rarely |
| Collections / treatment / write-off | BAN | `fact_treatment` + status timeline | Yes — shared party, cycles |
| Involuntary churn | BAN | status + delinquency features | Light (household contagion) |
| Voluntary churn / port-out | CTN | status + usage slope + competitor | Yes — social influence |
| Retention / marketing | both | feature store + touches | Communities / ego graphs |
| Sales / upgrades | CTN + device | `fact_order` | Product affinity (optional) |
| Fraud | device/SIM/BAN | G4 | Yes — short patterns |
| Usage / “transactional” | CTN × day | aggregated facts | Only as monthly pairs |

“Dynamic” means **event tables + monthly snapshots**, not a live mutating graph. Rebuild PGQ overlays after each load (`CREATE OR REPLACE PROPERTY GRAPH`). The overlay is cheap; the tables are not.

---

## Performance techniques that actually matter here

1. **Pre-aggregate before you graph.** Daily usage and monthly call pairs, never raw CDR edges.
2. **Multiple graphs, current edges only.** Put `WHERE is_current` into the edge *table* (or a view) that PGQ reads. Don’t ask MATCH to filter 10 years of SCD2.
3. **Bound every path.** `{1,3}` or `{1,5}`. Telco money-laundering / SIM-swap patterns are short.
4. **Materialize algorithm output.** Run Louvain / PageRank once per month into `feat_*` columns. Don’t call Onager inside a scoring job.
5. **Keep hot keys numeric.** Join BAN/CTN as `BIGINT` surrogate keys.
6. **Don’t over-index.** ART PKs on dims; facts win from sort order + partition prune, not B-trees.
7. **Two DuckDB versions if you must use PGQ.** 1.4.4 + DuckPGQ for graph notebooks; latest 1.5.x / upcoming 2.0 for marts and recursive CTEs. Attach the same Parquet from both.
8. **Recursive CTE + `USING KEY` for anything that must not break** (production IVC pipeline). PGQ for exploration and fraud pattern hunting.
9. **LadybugDB only if** you outgrow PGQ on multi-hop Cypher and want an embedded graph engine that can attach DuckDB/Parquet. Don’t start there.

---

## Recommended local stack (your machine)

```
Parquet / DuckLake          immutable events
     │
DuckDB 1.5+ / 2.0           marts, features, LightGBM extracts, recursive CTE
     │
DuckDB 1.4.4 + DuckPGQ      pattern notebooks (identity, collections, fraud)
     │
Onager                      Louvain, PageRank, ego, link prediction on monthly edges
     │
Polars / Parquet            feature engineering you already do
```

That matches how you already work (ensembles, explainability, local LLM on extracts). The graph layer should **emit features**, not become the model runtime.

---

## What is *not* ready (don’t bet the design on it)

- DuckPGQ as a supported, version-locked core extension  
- DuckPGQ graph algorithms as reliable (known CSR bug)  
- Unbounded path queries on subscriber call graphs  
- One property graph that is “the AT&T digital twin”  
- Storing every marketing touch as a first-class edge  
- Local full-population CDR  

Those are the failure modes.

---

## Build order (so it stays dynamic)

1. **Identity + SCD bridges** (BAN, CTN, device, SIM) — this unlocks every domain.  
2. **Monthly account + subscriber feature tables** — this unlocks IVC/VC/collections models you already run.  
3. **Status and treatment timelines** — collections + involuntary churn.  
4. **`fact_call_pair_month` + Onager communities** — retention / viral churn.  
5. **DuckPGQ identity + collections graphs** on a 1.4.4 notebook against the same Parquet.  
6. **Fraud device/SIM graph** once identity is clean.  
7. Only then consider LadybugDB or DuckDB 2.0 recursive CTE as the long-term path engine.

---

## Verdict

The latest viable technique is **graph-on-relational**: DuckLake/Parquet for events, typed BAN/CTN marts for performance, SQL/PGQ as a disposable overlay, Onager for community/centrality, recursive CTEs for anything that has to survive a DuckDB upgrade.

That is feasible on your hardware for a **portfolio-scale, feature-oriented** Mobility lab. It is not feasible as a local replica of national CDR with one giant DuckPGQ graph.

If you want a next step, the highest-leverage artifact is a concrete DDL pack: identity + SCD2 bridges + `fs_account_month` / `fs_subscriber_month` + the four `CREATE PROPERTY GRAPH` statements, sized for a sample BAN population. I can write that next.
