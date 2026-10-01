import subprocess
import sys
import time

def run_step(command, step_name):
    print(f"\n{'='*50}\nStarting Step: {step_name}\n{'='*50}")
    t0 = time.time()
    try:
        result = subprocess.run(command, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Pipeline failed at step: {step_name}")
        print(f"Error code: {e.returncode}")
        sys.exit(e.returncode)
    
    t1 = time.time()
    print(f"\n[SUCCESS] Completed {step_name} in {t1 - t0:.1f}s")

def main():
    start_time = time.time()
    
    # 1. Clean the raw CSV/Parquet files and build edges
    run_step(
        command=["uv", "run", "python", "code/scripts/01_clean_pipeline.py"],
        step_name="Data Cleaning & Edge Generation"
    )
    
    # 2. Build the graph embeddings & kNN clustering (TF-IDF Leiden Model)
    run_step(
        command=["uv", "run", "python", "code/scripts/02_build_graph.py"],
        step_name="TF-IDF Bipartite Graph Pipeline"
    )
    
    # # 3. Create enrichment layers for the Streamlit dashboard
    # run_step(
    #     command=["uv", "run", "python", "code/scripts/03_graph_insight_enrichment.py"],
    #     step_name="Graph Insight Enrichment"
    # )
    
    # # 4. Generate the final output HTML report
    # run_step(
    #     command=["uv", "run", "python", "code/scripts/04_gen_html_full.py"],
    #     step_name="HTML Report Generation"
    # )
    
    total_time = time.time() - start_time
    print(f"\n{'='*50}\nPipeline completed successfully in {total_time:.1f}s!\nOutputs generated in data/clean/ and Modeling/")

if __name__ == "__main__":
    main()
