"""
TF-IDF Weighted Bipartite Graph Pipeline (Alternative to FastRP)

This script replaces the FastRP projection with a TF-IDF weighted 
bipartite approach to down-weight high-volume generic behaviors.
"""
import time
from pathlib import Path

import numpy as np
import polars as pl
from scipy import sparse 
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.decomposition import TruncatedSVD
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize
import igraph as ig
import leidenalg

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "clean"
EDGE_DIR = DATA_DIR / "edges"
OUT_PATH = DATA_DIR / "new_gds_features.parquet"

def build_normalized_matrix():
    print("Loading data...")
    members_df = pl.read_parquet(DATA_DIR / 'member_analysis.parquet', columns=['member_key', 'totalpoints'])
    member_keys = members_df['member_key'].to_list()
    m_to_idx = {k: i for i, k in enumerate(member_keys)}
    
    edge_files = [
        EDGE_DIR / "uses_channel.parquet",
        EDGE_DIR / "shops_at.parquet",
        EDGE_DIR / "visits.parquet",
        EDGE_DIR / "holds.parquet",
        EDGE_DIR / "uses_bff.parquet",
    ]
    
    rows, cols, values = [], [], []
    entity_to_idx = {}
    current_entity_idx = 0
    
    print("Building weighted bipartite matrix...")
    for f in edge_files:
        if not f.exists(): continue
        df = pl.read_parquet(f)
        
        mem_col = "member_key"
        entity_cols = [c for c in df.columns if c not in [mem_col, 'count', 'transaction_count', 'total_amt', 'weight', 'visit_count']]
        if not entity_cols: continue
        entity_col = entity_cols[0]
        
        df = df.filter(pl.col(mem_col).is_in(m_to_idx.keys()))
        
        # Determine weight column
        if 'transaction_count' in df.columns:
            w_col = 'transaction_count'
        elif 'count' in df.columns:
            w_col = 'count'
        elif 'visit_count' in df.columns:
            w_col = 'visit_count'
        else:
            w_col = None
            
        m_vals = df[mem_col].to_list()
        e_vals = df[entity_col].to_list()
        w_vals = df[w_col].to_list() if w_col else [1.0] * len(m_vals)
        
        for m, e, w in zip(m_vals, e_vals, w_vals):
            e_str = f"{f.stem}_{e}"
            if e_str not in entity_to_idx:
                entity_to_idx[e_str] = current_entity_idx
                current_entity_idx += 1
                
            rows.append(m_to_idx[m])
            cols.append(entity_to_idx[e_str])
            values.append(w)
            
    # Remove duplicates by summing weights
    mat = sparse.coo_matrix((values, (rows, cols)), shape=(len(m_to_idx), len(entity_to_idx)))
    mat = sparse.csr_matrix(mat.tocsr())
    
    print(f"Applying TF-IDF to {mat.shape} matrix...")
    tfidf = TfidfTransformer()
    mat_tfidf = tfidf.fit_transform(mat)
    
    print("Running TruncatedSVD to reduce dimensions to 127...")
    svd = TruncatedSVD(n_components=127, random_state=42)
    mat_svd = svd.fit_transform(mat_tfidf)
    
    # Extract engagement score and normalize
    eng = members_df['totalpoints'].fill_null(0).to_numpy()
    eng_scaled = eng / (eng.max() + 1e-9)
    eng_scaled = eng_scaled.reshape(-1, 1) * 0.1 # Explicitly down-weight
    
    print("Concatenating engagement score...")
    final_emb = np.hstack([mat_svd, eng_scaled]) # 128 dimensions total
    
    # Normalize for cosine similarity
    final_emb = normalize(final_emb, norm='l2', axis=1)
    
    print("Building kNN Graph (k=15)...")
    nn = NearestNeighbors(n_neighbors=15, algorithm='brute', metric='cosine')
    nn.fit(final_emb)
    distances, indices = nn.kneighbors(final_emb)
    
    print("Constructing igraph...")
    n_members = len(m_to_idx)
    edges = []
    weights = []
    for i in range(n_members):
        for j, dist in zip(indices[i], distances[i]):
            if i != j:
                edges.append((i, j))
                weights.append(1.0 - dist) # Cosine similarity
                
    g = ig.Graph(n=n_members, edges=edges, directed=False)
    g.es['weight'] = weights
    g.simplify(combine_edges='max') # Remove multi-edges securely
    
    print("Running Leiden community detection...")
    partition = leidenalg.find_partition(
        g, 
        leidenalg.ModularityVertexPartition,
        weights='weight', 
        n_iterations=2,
        seed=42
    )
    leiden_ids = partition.membership
    
    print("Calculating Centrality metrics...")
    pr = g.pagerank(weights='weight')
    deg = g.strength(weights='weight') # Weighted degree
    
    print("Exporting data...")
    result_df = pl.DataFrame({
        'member_key': member_keys,
        'leiden_id': leiden_ids,
        'louvain_id': leiden_ids, # Placeholder to avoid breaking downstreams
        'pagerank': pr,
        'degree_centrality': deg,
    })
    
    emb_df = pl.DataFrame(final_emb, schema=[f"emb_{i}" for i in range(128)])
    
    final_df = pl.concat([result_df, emb_df], how="horizontal")
    
    # Join engagement_level from original for reference if needed
    eng_df = members_df.select(['member_key', 'totalpoints'])
    # In full pipeline we use member_personas_final, here we just output gds format
    
    final_df.write_parquet(OUT_PATH)
    print(f"Success! Output saved to: {OUT_PATH}")

if __name__ == "__main__":
    t0 = time.time()
    build_normalized_matrix()
    print(f"Total time: {time.time() - t0:.1f}s")
