import os
import glob
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.backends.backend_pdf import PdfPages

def main():
    # File paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.abspath(os.path.join(script_dir, '..', 'merge'))
    output_dir = script_dir
    
    csv_files = glob.glob(os.path.join(input_dir, '*.csv'))
    if not csv_files:
        print(f"No CSV files found in {input_dir}")
        return
        
    sns.set_theme(style="whitegrid")
    
    ignore_cols = {
        'id', 'benchmark_type', 'item_name_or_query', 'query', 'doc_name',
        'error', 'response', 'cw_request_id', 'topic', 'status_code',
        'invocation_idx', 'mode', 's3_key', 'metric_handler_total_objectKey',
        'wall_time', 'upload_time_ms', 'metric_handler_start_wallTimeMs',
        'metric_handler_start_ms'
    }
    
    # First pass: find global maximum Y across all files to normalize the Y scale
    global_max_y = 0
    file_dfs = {}
    file_metrics = {}
    
    for file in csv_files:
        df = pd.read_csv(file)
        if 'id' not in df.columns:
            print(f"  Skipping {file} due to missing 'id' column.")
            continue
            
        numeric_cols = df.select_dtypes(include=['number']).columns
        available_metrics = [c for c in numeric_cols if c not in ignore_cols and not c.endswith('_ms_ms') and 'epoch' not in c.lower()]
        available_metrics = [c for c in available_metrics if not c.startswith('cw_memory') and not c.startswith('cw_max_memory') and not c == 'metric_retrieval_total_numDocs']
        
        if not available_metrics:
            print(f"  No valid metric columns found to plot in {file}.")
            continue
            
        file_dfs[file] = df
        file_metrics[file] = available_metrics
        
        file_max = df[available_metrics].max().max()
        if pd.notna(file_max) and file_max > global_max_y:
            global_max_y = file_max
            
    y_upper_limit = global_max_y * 1.05 if global_max_y > 0 else None
    
    # Second pass: generate PDFs
    for file, df in file_dfs.items():
        basename = os.path.basename(file)
        name_no_ext = os.path.splitext(basename)[0]
        output_pdf = os.path.join(output_dir, f"aggregated_boxplot_{name_no_ext}.pdf")
        
        available_metrics = file_metrics[file]
        print(f"Processing {file}...")
        print(f"  Metrics to plot: {available_metrics}")
        
        with PdfPages(output_pdf) as pdf:
            # Transform data from wide to long format for all rows
            df_melted = df.melt(
                id_vars=['id'],
                value_vars=available_metrics,
                var_name='metric',
                value_name='latency_ms'
            )
            
            # Create the plot
            plt.figure(figsize=(14, 8))
            
            sns.boxplot(
                data=df_melted, 
                x='metric', 
                y='latency_ms',
                palette="Set2"
            )
            
            if y_upper_limit is not None:
                plt.ylim(-y_upper_limit * 0.02, y_upper_limit)
            
            plt.title(f"Aggregated Latency Distribution by Component\nDataset: {name_no_ext} (All Tests)", fontsize=16, pad=20)
            plt.xlabel("Component / Operation", fontsize=12)
            plt.ylabel("Latency (ms)", fontsize=12)
            plt.xticks(rotation=45, ha='right', fontsize=10)
            plt.tight_layout()
            
            pdf.savefig()
            plt.close()
                
        print(f"  Saved {output_pdf}")

if __name__ == '__main__':
    main()
