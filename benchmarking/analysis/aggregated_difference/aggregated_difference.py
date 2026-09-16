import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.backends.backend_pdf import PdfPages

def aggregate_differences(test_type, lambda_csv, microvm_csv, output_pdf):
    if not os.path.exists(lambda_csv) or not os.path.exists(microvm_csv):
        print(f"Skipping {test_type}: Required CSV files not found.")
        return
        
    print(f"\nProcessing {test_type} aggregated differences...")
    
    df_lambda = pd.read_csv(lambda_csv)
    df_microvm = pd.read_csv(microvm_csv)
    
    # Identify common numeric columns
    num_cols_l = set(df_lambda.select_dtypes(include=['number']).columns)
    num_cols_m = set(df_microvm.select_dtypes(include=['number']).columns)
    
    ignore_cols = {
        'id', 'invocation_idx', 'status_code', 'start_epoch_ms', 'wall_time', 
        'upload_time_ms', 'metric_retrieval_total_numDocs',
        'metric_handler_start_wallTimeMs', 'metric_handler_start_ms'
    }
    
    # Filter only meaningful metrics that exist in BOTH datasets
    common_metrics = sorted([
        c for c in num_cols_l.intersection(num_cols_m) 
        if c not in ignore_cols 
        and not c.startswith('cw_') 
        and 'epoch' not in c
    ])
    
    if not common_metrics:
        print(f"No common metrics found for {test_type}.")
        return
        
    print(f"Common metrics to plot for {test_type}: {common_metrics}")
    
    # Lambda grouping (average per test ID)
    avg_l = df_lambda.groupby('id')[common_metrics].mean().reset_index()
    
    # MicroVM grouping (average per test ID)
    avg_m = df_microvm.groupby('id')[common_metrics].mean().reset_index()
    
    # Merge on ID
    merged = pd.merge(
        avg_l, 
        avg_m, 
        on='id', 
        suffixes=('_lambda', '_microvm')
    )
    
    if merged.empty:
        print(f"No overlapping IDs in {test_type}. Skipping.")
        return
        
    # Calculate average difference for each metric across all test IDs
    summary_data = []
    for metric in common_metrics:
        diff_series = merged[f'{metric}_microvm'] - merged[f'{metric}_lambda']
        avg_diff = diff_series.mean()
        summary_data.append({
            'Metric': metric,
            'Avg_Difference': avg_diff
        })
        
    df_summary = pd.DataFrame(summary_data)
    
    sns.set_theme(style="whitegrid")
    
    from matplotlib.patches import Patch
    with PdfPages(output_pdf) as pdf:
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Color bars based on sign: Blue for MicroVM faster (< 0), Orange for Lambda faster (> 0)
        colors = ['#1f77b4' if val < 0 else '#ff7f0e' for val in df_summary['Avg_Difference']]
        
        bars = ax.bar(df_summary['Metric'], df_summary['Avg_Difference'], color=colors, edgecolor='black', alpha=0.85)
        
        plt.ylabel("Average Difference (ms)", fontsize=12)
            
        # Draw a horizontal line at 0
        plt.axhline(0, color='black', linewidth=1.5, linestyle='--')
        
        # Add value labels on each bar
        for bar in bars:
            height = bar.get_height()
            va = 'bottom' if height >= 0 else 'top'
            val_str = f"{height:+.1f} ms"
            ax.annotate(
                val_str,
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 3 if height >= 0 else -12),
                textcoords="offset points",
                ha='center', va=va, fontsize=9, fontweight='bold'
            )
            
        plt.title(f"{test_type.capitalize()} Component Comparison\nOverall Average Difference (MicroVM - Lambda) Across All Tests", fontsize=16, pad=20)
        plt.xlabel("Component / Operation", fontsize=12)
        plt.xticks(rotation=45, ha='right', fontsize=10)
        
        legend_elements = [
            Patch(facecolor='#1f77b4', edgecolor='black', label='< 0 : MicroVM is Faster'),
            Patch(facecolor='#ff7f0e', edgecolor='black', label='> 0 : Lambda is Faster')
        ]
        plt.legend(handles=legend_elements, loc='upper right', fontsize=11)
        
        plt.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)
            
    print(f"Saved {output_pdf}")

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.abspath(os.path.join(script_dir, '..', 'merge'))
    output_dir = script_dir
    
    # 1. Ingest Comparison
    aggregate_differences(
        test_type='ingest',
        lambda_csv=os.path.join(base_dir, 'lambda_ingest_merged.csv'),
        microvm_csv=os.path.join(base_dir, 'microvm_ingest_merged.csv'),
        output_pdf=os.path.join(output_dir, 'aggregated_difference_ingest.pdf')
    )
    
    # 2. Query Comparison
    aggregate_differences(
        test_type='query',
        lambda_csv=os.path.join(base_dir, 'lambda_query_merged.csv'),
        microvm_csv=os.path.join(base_dir, 'microvm_query_merged.csv'),
        output_pdf=os.path.join(output_dir, 'aggregated_difference_query.pdf')
    )

if __name__ == '__main__':
    main()
