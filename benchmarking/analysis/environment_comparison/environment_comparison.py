import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

def compare_environments(test_type, lambda_csv, microvm_csv, output_pdf):
    if not os.path.exists(lambda_csv) or not os.path.exists(microvm_csv):
        print(f"Skipping {test_type}: Required CSV files not found.")
        return
        
    print(f"\nProcessing {test_type} comparison...")
    
    df_lambda = pd.read_csv(lambda_csv)
    df_microvm = pd.read_csv(microvm_csv)
    
    # Identify common numeric columns
    num_cols_l = set(df_lambda.select_dtypes(include=['number']).columns)
    num_cols_m = set(df_microvm.select_dtypes(include=['number']).columns)
    
    ignore_cols = {
        'id', 'invocation_idx', 'status_code', 'start_epoch_ms', 'wall_time', 
        'upload_time_ms', 'metric_retrieval_total_numDocs'
    }
    
    # Filter only meaningful metrics that exist in BOTH datasets
    common_metrics = sorted([
        c for c in num_cols_l.intersection(num_cols_m) 
        if c not in ignore_cols 
        and not c.startswith('cw_') 
        and 'epoch' not in c 
        and 'time' not in c
    ])
    
    if not common_metrics:
        print(f"No common metrics found for {test_type}.")
        return
        
    print(f"Common metrics to plot for {test_type}: {common_metrics}")
    
    sns.set_theme(style="whitegrid")
    
    with PdfPages(output_pdf) as pdf:
        for metric in common_metrics:
            # Group by ID and take the average
            # Assuming 'item_name_or_query' is present in both after previous header adjustments
            item_col = 'item_name_or_query' if 'item_name_or_query' in df_lambda.columns else None
            
            # Lambda grouping
            if item_col:
                avg_l = df_lambda.groupby(['id', item_col])[metric].mean().reset_index()
            else:
                avg_l = df_lambda.groupby('id')[metric].mean().reset_index()
                
            # MicroVM grouping
            avg_m = df_microvm.groupby('id')[metric].mean().reset_index()
            
            # Merge on ID
            merged = pd.merge(
                avg_l, 
                avg_m, 
                on='id', 
                suffixes=('_lambda', '_microvm')
            )
            
            if merged.empty:
                print(f"No overlapping IDs for metric {metric} in {test_type}. Skipping.")
                continue
            
            # Calculate the difference: MicroVM - Lambda
            # Negative means MicroVM is faster, Positive means MicroVM is slower
            diff_col = 'Difference (MicroVM - Lambda) ms'
            merged[diff_col] = merged[f'{metric}_microvm'] - merged[f'{metric}_lambda']
            
            # Ensure ID is treated as categorical for the X-axis
            merged['id'] = merged['id'].astype(str)
            
            # Determine if we need a log scale based on the absolute maximum difference
            max_abs_diff = merged[diff_col].abs().max()
            use_log_scale = max_abs_diff > 100
            
            plt.figure(figsize=(14, 7))
            
            # Create a color palette based on whether the value is positive or negative
            colors = ['#ff7f0e' if val > 0 else '#1f77b4' for val in merged[diff_col]]
            
            ax = sns.barplot(
                data=merged,
                x='id',
                y=diff_col,
                palette=colors
            )
            
            if use_log_scale:
                # symlog handles both positive and negative extremes beautifully
                ax.set_yscale('symlog', linthresh=10.0)
                plt.ylabel("Difference (ms) [symlog scale]", fontsize=12)
            else:
                plt.ylabel("Difference (ms)", fontsize=12)
            
            # Add a horizontal line at 0 for clarity
            plt.axhline(0, color='black', linewidth=1)
            
            plt.title(f"{test_type.capitalize()} Comparison: {metric}\nDifference (MicroVM - Lambda)", fontsize=14, pad=20)
            plt.xlabel("Test ID", fontsize=12)
            
            # Add a custom legend to explain the colors
            from matplotlib.patches import Patch
            legend_elements = [
                Patch(facecolor='#1f77b4', label='MicroVM Faster (< 0)'),
                Patch(facecolor='#ff7f0e', label='Lambda Faster (> 0)')
            ]
            plt.legend(handles=legend_elements, loc='upper right')
            
            # If we have item names, add a text box or table at the bottom to map ID -> item_name
            if item_col:
                # We can place a small legend or just let the user refer to the CSV. 
                # Given space constraints, printing them out or saving them alongside is better,
                # but we can try to fit it in the plot layout if there aren't too many.
                pass
                
            plt.tight_layout()
            pdf.savefig()
            plt.close()
            
    print(f"Saved {output_pdf}")

def main():
    base_dir = '../merge'
    output_dir = '.'
    
    # 1. Ingest Comparison
    compare_environments(
        test_type='ingest',
        lambda_csv=os.path.join(base_dir, 'lambda_ingest_merged.csv'),
        microvm_csv=os.path.join(base_dir, 'microvm_ingest_merged.csv'),
        output_pdf=os.path.join(output_dir, 'environment_comparison_ingest.pdf')
    )
    
    # 2. Query Comparison
    compare_environments(
        test_type='query',
        lambda_csv=os.path.join(base_dir, 'lambda_query_merged.csv'),
        microvm_csv=os.path.join(base_dir, 'microvm_query_merged.csv'),
        output_pdf=os.path.join(output_dir, 'environment_comparison_query.pdf')
    )

if __name__ == '__main__':
    main()
