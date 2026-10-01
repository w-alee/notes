"""
Clean Pipeline — UFCU Neo4j Capstone
=====================================
Reads raw parquet files, applies all cleaning/encoding transformations,
pre-aggregates transactions for graph edges, and writes to data/clean/.

Outputs:
  data/clean/member_attributes.parquet   (latest snapshot, cleaned demographics)
  data/clean/member_products.parquet     (latest snapshot, cleaned product indicators)
  data/clean/member_transactions_agg.parquet  (aggregated per member)
  data/clean/edges/                      (pre-built edge lists for Neo4j)
  data/clean/member_analysis.parquet     (merged analysis-ready dataset)

Usage:
  uv run python code/scripts/clean_pipeline.py
"""

from pathlib import Path
import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "parquet"
CLEAN_DIR = PROJECT_ROOT / "data" / "clean"
EDGE_DIR = CLEAN_DIR / "edges"

# ── Mapping dictionaries (ported from PCA_analysis_clean.ipynb) ──────────────

MARITAL_MARRIED = {"married likely", "married extremely likely"}
MARITAL_SINGLE = {"single likely, never married"}
MARITAL_UNKNOWN = {"unknown scored", "unknown not scored"}

HOMEOWNER_OWN = {
    "homeowner",
    "probable homeowner 90-100",
    "probable homeowner 80-89",
    "probable homeowner 70-79",
}
HOMEOWNER_RENT = {"probably renter", "renter"}

ATTRITION_MAP = {
    "Extremely Unlikely": -3,
    "Very Unlikely": -2,
    "Unlikely": -1,
    "Neutral": 0,
    "Likely": 1,
    "Very Likely": 2,
    "Extremely Likely": 3,
}

# BFF indicator columns → human-readable names
BFF_COLS = [
    "member_debit_card_active_usage_ind",
    "member_credit_card_active_usage_ind",
    "ach_deposit_active_ind",
    "external_transfer_active_ind",
    "digital_banking_enrollment_ind",
    "atm_on_us_active_ind",
    "mobile_check_deposit_active_ind",
    "business_remote_deposit_active_ind",
    "bill_pay_active_ind",
    "check_withdrawal_active_ind",
    "internal_transfer_active_ind",
    "zelle_active_ind",
    "outgoing_wire_active_ind",
    "personal_financial_management_enrollment_ind",
    "investment_account_active_ind",
]
BFF_NAMES = [
    "Debit Card",
    "Credit Card",
    "ACH Deposit",
    "External Transfer",
    "Digital Banking",
    "ATM",
    "Mobile Check Deposit",
    "Remote Deposit",
    "Bill Pay",
    "Checks",
    "Internal Transfer",
    "Zelle",
    "Wire",
    "PFM",
    "Investment",
]

PRODUCT_COUNT_COLS = [
    "savings_cnt",
    "checking_cnt",
    "money_market_cnt",
    "certificate_cnt",
    "ira_cnt",
    "business_deposit_cnt",
    "public_funds_deposit_cnt",
    "installment_cnt",
    "visa_cnt",
    "mortgage_cnt",
    "equity_cnt",
    "indirect_cnt",
    "business_loan_cnt",
    "other_loan_cnt",
    "total_investment_cnt",
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _latest_snapshot(df: pl.DataFrame) -> pl.DataFrame:
    """Keep only the most recent point_in_time_tmstp row per member_key."""
    return (
        df.sort("point_in_time_tmstp", descending=True)
        .unique(subset=["member_key"], keep="first")
    )


def _map_marital(val: str | None) -> str:
    if val is None:
        return "unknown"
    lower = val.strip().lower()
    if lower in MARITAL_MARRIED:
        return "married"
    if lower in MARITAL_SINGLE:
        return "single"
    return "unknown"


def _map_homeownership(val: str | None) -> str:
    if val is None:
        return "unknown"
    lower = val.strip().lower()
    if lower in HOMEOWNER_OWN:
        return "homeowner"
    if lower in HOMEOWNER_RENT:
        return "renter"
    return "unknown"


def _extract_age_midpoint(val: str | None) -> float | None:
    """Parse agebins like '25-30', '76+', '<=18' to midpoint."""
    if val is None:
        return None
    s = val.strip()
    if "-" in s:
        parts = s.split("-")
        try:
            return (float(parts[0]) + float(parts[1])) / 2
        except (ValueError, IndexError):
            return None
    if s.endswith("+"):
        try:
            return float(s[:-1])
        except ValueError:
            return None
    if s.startswith("<="):
        try:
            return float(s[2:])
        except ValueError:
            return None
    return None


def _extract_income_midpoint(val: str | None) -> float | None:
    """Parse income ranges like '$25,000 - $34,999' or '$150,000+' to midpoint."""
    if val is None:
        return None
    s = val.strip()
    if "unknown" in s.lower():
        return None
    clean = s.replace("$", "").replace(",", "").strip()
    if "-" in clean:
        parts = clean.split("-")
        try:
            return (float(parts[0].strip()) + float(parts[1].strip())) / 2
        except (ValueError, IndexError):
            return None
    if clean.endswith("+"):
        try:
            return float(clean[:-1])
        except ValueError:
            return None
    return None


def _extract_leading_int(val: str | None) -> int | None:
    """Extract leading integer from strings like '2 persons', '0 children'."""
    if val is None:
        return None
    import re
    m = re.search(r"(\d+)", val.strip())
    return int(m.group(1)) if m else None


# ── Step 1: Clean member_attributes ─────────────────────────────────────────


def clean_attributes(df: pl.DataFrame) -> pl.DataFrame:
    """Clean member_attributes: latest snapshot, encode demographics."""
    df = _latest_snapshot(df)

    # Normalize engagement_level casing
    df = df.with_columns(pl.col("engagement_level").str.to_titlecase())

    # Marital status → 3-level
    df = df.with_columns(
        pl.col("experian_estimated_marital_status")
        .map_elements(_map_marital, return_dtype=pl.Utf8)
        .alias("marital_status_clean")
    )

    # Homeownership → 3-level
    df = df.with_columns(
        pl.col("experian_estimated_homeownership_indicator")
        .map_elements(_map_homeownership, return_dtype=pl.Utf8)
        .alias("homeownership_clean")
    )

    # Attrition → numeric
    df = df.with_columns(
        pl.col("attrition_likelihood")
        .replace_strict(ATTRITION_MAP, default=None, return_dtype=pl.Int8)
        .alias("attrition_score")
    )

    # Age midpoint
    df = df.with_columns(
        pl.col("agebins")
        .map_elements(_extract_age_midpoint, return_dtype=pl.Float64)
        .alias("age_midpoint")
    )

    # Income midpoint
    df = df.with_columns(
        pl.col("experian_estimated_household_income_range")
        .map_elements(_extract_income_midpoint, return_dtype=pl.Float64)
        .alias("income_midpoint")
    )

    # Adults / children → numeric
    df = df.with_columns(
        pl.col("experian_estimated_adults_in_residence")
        .map_elements(_extract_leading_int, return_dtype=pl.Int64)
        .alias("adults_in_residence"),
        pl.col("experian_estimated_children_in_residence")
        .map_elements(_extract_leading_int, return_dtype=pl.Int64)
        .alias("children_in_residence"),
    )

    # Gender: keep as-is but normalize
    df = df.with_columns(
        pl.col("experian_estimated_gender").str.to_titlecase().alias("gender_clean")
    )

    # Drop raw experian columns superseded by clean versions
    raw_drop = [
        "experian_estimated_marital_status",
        "experian_estimated_homeownership_indicator",
        "experian_estimated_household_income_range",
        "agebins",
        "experian_estimated_adults_in_residence",
        "experian_estimated_children_in_residence",
        "experian_estimated_gender",
        "attrition_likelihood",
        "member_tenure_bucket",  # redundant with member_tenure numeric
    ]
    df = df.drop([c for c in raw_drop if c in df.columns])

    return df


# ── Step 2: Clean member_products ───────────────────────────────────────────


def clean_products(df: pl.DataFrame) -> pl.DataFrame:
    """Clean member_products: latest snapshot, normalize engagement casing."""
    df = _latest_snapshot(df)
    df = df.with_columns(pl.col("engagement_level").str.to_titlecase())
    return df


# ── Step 3: Pre-aggregate transactions ──────────────────────────────────────


def aggregate_transactions(df: pl.DataFrame) -> pl.DataFrame:
    """Aggregate transactions per member for graph properties."""
    # Fix known data quality issues
    df = df.rename({"cleansed merchant name": "cleansed_merchant_name"})
    df = df.with_columns(pl.col("fee_amt").cast(pl.Float64, strict=False))

    agg = df.group_by("member_key").agg(
        # Volume
        pl.len().alias("transaction_count"),
        pl.col("transaction_amt").sum().alias("total_transaction_amt"),
        pl.col("transaction_amt").mean().alias("avg_transaction_amt"),
        pl.col("transaction_amt").median().alias("median_transaction_amt"),
        # Channels
        pl.col("transaction_channel_desc").n_unique().alias("n_channels"),
        # Merchants
        pl.col("merchant_category_desc").drop_nulls().n_unique().alias("n_merchant_categories"),
        pl.col("cleansed_merchant_name").drop_nulls().n_unique().alias("n_unique_merchants"),
        # Accounts
        pl.col("account_key").n_unique().alias("n_accounts"),
        # Branches
        pl.col("transaction_branch_id").drop_nulls().n_unique().alias("n_branches_visited"),
        # Fees
        pl.col("fee_amt").sum().alias("total_fees"),
        # Date range
        pl.col("transaction_tmstp").min().alias("first_transaction"),
        pl.col("transaction_tmstp").max().alias("last_transaction"),
        # Debit vs Credit
        pl.col("debit_credit_cd").filter(pl.col("debit_credit_cd") == "DBT").len().alias("n_debits"),
        pl.col("debit_credit_cd").filter(pl.col("debit_credit_cd") == "CRD").len().alias("n_credits"),
    )
    return agg


# ── Step 4: Build edge lists ────────────────────────────────────────────────


def build_edges(trans: pl.DataFrame, prods: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Build graph edge CSVs from cleaned data."""
    # Fix data quality issues for edge building
    trans = trans.rename({"cleansed merchant name": "cleansed_merchant_name"})
    trans = trans.with_columns(pl.col("fee_amt").cast(pl.Float64, strict=False))

    edges = {}

    # USES_CHANNEL: Member → Channel (aggregated)
    edges["uses_channel"] = (
        trans.group_by(["member_key", "transaction_channel_desc"])
        .agg(
            pl.len().alias("transaction_count"),
            pl.col("transaction_amt").sum().alias("total_amt"),
        )
        .filter(pl.col("transaction_channel_desc").is_not_null())
        .rename({"transaction_channel_desc": "channel"})
    )

    # SHOPS_AT: Member → MerchantCategory (aggregated)
    edges["shops_at"] = (
        trans.filter(pl.col("merchant_category_desc").is_not_null())
        .group_by(["member_key", "merchant_category_desc"])
        .agg(
            pl.len().alias("transaction_count"),
            pl.col("transaction_amt").sum().alias("total_amt"),
        )
        .rename({"merchant_category_desc": "merchant_category"})
    )

    # VISITS: Member → Branch
    edges["visits"] = (
        trans.filter(pl.col("transaction_branch_id").is_not_null())
        .group_by(["member_key", "transaction_branch_id"])
        .agg(pl.len().alias("visit_count"))
        .rename({"transaction_branch_id": "branch_id"})
    )

    # HOLDS: Member → Product (from products, one edge per product type held)
    holds_records = []
    for col in PRODUCT_COUNT_COLS:
        if col not in prods.columns:
            continue
        product_name = col.replace("_cnt", "").replace("_", " ").title()
        holders = prods.filter(pl.col(col) > 0).select(
            pl.col("member_key"),
            pl.lit(product_name).alias("product_type"),
            pl.col(col).alias("count"),
        )
        holds_records.append(holders)
    if holds_records:
        edges["holds"] = pl.concat(holds_records)

    # USES_BFF: Member → BFF feature (from products)
    bff_records = []
    for col, name in zip(BFF_COLS, BFF_NAMES):
        if col not in prods.columns:
            continue
        active = prods.filter(pl.col(col) == True).select(
            pl.col("member_key"),
            pl.lit(name).alias("bff_feature"),
        )
        bff_records.append(active)
    if bff_records:
        edges["uses_bff"] = pl.concat(bff_records)

    return edges


# ── Step 5: Merge for analysis dataset ──────────────────────────────────────


def build_analysis_dataset(
    attrs: pl.DataFrame, prods: pl.DataFrame, trans_agg: pl.DataFrame
) -> pl.DataFrame:
    """Merge cleaned attrs + prods + aggregated trans into one analysis table."""
    # Select key product columns for merge
    prod_cols = (
        ["member_key", "engagement_level"]
        + [c for c in PRODUCT_COUNT_COLS if c in prods.columns]
        + [c for c in BFF_COLS if c in prods.columns]
        + [
            c
            for c in prods.columns
            if c.endswith("_amt") or c.endswith("_90_days_cnt")
        ]
    )
    # Deduplicate column list
    prod_cols = list(dict.fromkeys(prod_cols))
    prods_slim = prods.select([c for c in prod_cols if c in prods.columns])

    merged = attrs.join(prods_slim, on="member_key", how="left", suffix="_prods")
    merged = merged.join(trans_agg, on="member_key", how="left")
    return merged


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    EDGE_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading raw data...")
    attrs_raw = pl.read_parquet(RAW_DIR / "member_attributes.parquet")
    prods_raw = pl.read_parquet(RAW_DIR / "member_products.parquet")
    trans_raw = pl.read_parquet(RAW_DIR / "member_transactions.parquet")
    print(
        f"  attrs: {attrs_raw.shape}, prods: {prods_raw.shape}, trans: {trans_raw.shape}"
    )

    # Step 1
    print("Cleaning member_attributes...")
    attrs_clean = clean_attributes(attrs_raw)
    attrs_clean.write_parquet(CLEAN_DIR / "member_attributes.parquet")
    print(f"  → {attrs_clean.shape[0]:,} rows, {attrs_clean.shape[1]} cols")

    # Step 2
    print("Cleaning member_products...")
    prods_clean = clean_products(prods_raw)
    prods_clean.write_parquet(CLEAN_DIR / "member_products.parquet")
    print(f"  → {prods_clean.shape[0]:,} rows, {prods_clean.shape[1]} cols")

    # Step 3
    print("Aggregating transactions...")
    trans_agg = aggregate_transactions(trans_raw)
    trans_agg.write_parquet(CLEAN_DIR / "member_transactions_agg.parquet")
    print(f"  → {trans_agg.shape[0]:,} rows, {trans_agg.shape[1]} cols")

    # Step 4
    print("Building edge lists...")
    edges = build_edges(trans_raw, prods_clean)
    for name, edge_df in edges.items():
        edge_df.write_parquet(EDGE_DIR / f"{name}.parquet")
        print(f"  {name}: {edge_df.shape[0]:,} edges")

    # Step 5
    print("Building analysis dataset...")
    analysis = build_analysis_dataset(attrs_clean, prods_clean, trans_agg)
    analysis.write_parquet(CLEAN_DIR / "member_analysis.parquet")
    print(f"  → {analysis.shape[0]:,} rows, {analysis.shape[1]} cols")

    print("\nDone! Clean data written to", CLEAN_DIR)


if __name__ == "__main__":
    main()
