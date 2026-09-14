import os
import glob
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.backends.backend_pdf import PdfPages

def main():
    # File paths
    input_dir = '../merge'
    output_dir = '.'
    
    csv_files = glob.glob(os.path.join(input_dir, '*.csv'))
    if not csv_files:
        print(f"No CSV files found in {input_dir}")
        return
        
    # Set up seaborn style
    sns.set_theme(style="whitegrid")
    
    # Columns we should NEVER plot
    ignore_cols = {
        'id', 'benchmark_type', 'item_name_or_query', 'query', 'doc_name',
        'error', 'response', 'cw_request_id', 'topic', 'status_code',
        'invocation_idx', 'mode', 's3_key', 'metric_handler_total_objectKey'
    }
    
    for file in csv_files:
        basename = os.path.basename(file)
        name_no_ext = os.path.splitext(basename)[0]
        output_pdf = os.path.join(output_dir, f"outliers_boxplot_{name_no_ext}.pdf")
        
        print(f"Processing {file}...")
        df = pd.read_csv(file)
        
        if 'id' not in df.columns:
            print(f"  Skipping {file} due to missing 'id' column.")
            continue
            
        # Determine numeric columns for Y axis
        numeric_cols = df.select_dtypes(include=['number']).columns
        available_metrics = [c for c in numeric_cols if c not in ignore_cols and not c.endswith('_ms_ms') and 'epoch' not in c.lower() and 'time' not in c.lower()]
        
        # Also clean up any that might have been accidentally included
        # specifically some lambda cw_ columns
        available_metrics = [c for c in available_metrics if not c.startswith('cw_memory') and not c.startswith('cw_max_memory') and not c == 'metric_retrieval_total_numDocs']
        
        if not available_metrics:
            print(f"  No valid metric columns found to plot in {file}.")
            continue
            
        print(f"  Metrics to plot: {available_metrics}")
        
        # Try to find the item name column to use in title
        item_col = None
        for col in ['item_name_or_query', 'query', 'doc_name']:
            if col in df.columns:
                item_col = col
                break
                
        with PdfPages(output_pdf) as pdf:
            # Loop through each id
            for item_id in sorted(df['id'].unique()):
                df_id = df[df['id'] == item_id]
                
                # Transform data from wide to long format so seaborn can plot multiple columns easily
                df_melted = df_id.melt(
                    id_vars=['id'],
                    value_vars=available_metrics,
                    var_name='metric',
                    value_name='latency_ms'
                )
                
                # Create the plot
                plt.figure(figsize=(12, 6))
                
                # Boxplot automatically calculates percentiles and outliers
                sns.boxplot(
                    data=df_melted, 
                    x='metric', 
                    y='latency_ms',
                    palette="Set2"
                )
                
                # Beautify the plot
                item_name = df_id[item_col].iloc[0] if item_col else "Unknown"
                # Truncate title if too long
                if len(str(item_name)) > 80:
                    item_name = str(item_name)[:77] + "..."
                    
                plt.title(f"Latency Distribution by Component\nDataset: {name_no_ext} | ID: {item_id}\n({item_name})", fontsize=12)
                plt.xlabel("Component / Operation", fontsize=10)
                plt.ylabel("Latency (ms)", fontsize=10)
                plt.xticks(rotation=45, ha='right', fontsize=8)
                plt.tight_layout()
                
                # Save the current figure to the PDF
                pdf.savefig()
                plt.close()
                
        print(f"  Saved {output_pdf}")

if __name__ == '__main__':
    main()
