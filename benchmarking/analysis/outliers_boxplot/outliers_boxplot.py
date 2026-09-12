import os
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.backends.backend_pdf import PdfPages

def main():
    # File paths
    input_file = '../merge/merged.csv'
    output_dir = '.'
    
    # Read data
    print(f"Reading {input_file}...")
    df = pd.read_csv(input_file)
    
    # Columns of interest for the Y axis
    metrics = ['probe_e2e_ms', 'server_total_ms', 'resume_overhead_ms', 
               'bedrock_ms', 'lancedb_ms', 'file_read_ms']
    
    # Filter only available metrics
    available_metrics = [m for m in metrics if m in df.columns]
    
    if not available_metrics:
        print("No valid metric columns found to plot.")
        return
        
    print(f"Metrics to plot: {available_metrics}")

    # Set up seaborn style
    sns.set_theme(style="whitegrid")
    
    # If environment column is not present for some reason, mock it so code doesn't fail
    if 'environment' not in df.columns:
        print("Warning: 'environment' column not found, defaulting to 'unknown'.")
        df['environment'] = 'unknown'
        
    # Loop through each environment and create a separate PDF
    for env in df['environment'].unique():
        output_pdf = os.path.join(output_dir, f'outliers_boxplot_{env}.pdf')
        df_env = df[df['environment'] == env]
        print(f"\nProcessing environment: {env} -> saving to {output_pdf}")
        
        # Create the PDF for this environment
        with PdfPages(output_pdf) as pdf:
            
            # Loop through each benchmark_type (treated as separate datasets)
            for btype in df_env['benchmark_type'].unique():
                df_btype = df_env[df_env['benchmark_type'] == btype]
                
                # Loop through each id
                for item_id in sorted(df_btype['id'].unique()):
                    df_id = df_btype[df_btype['id'] == item_id]
                    
                    # Transform data from wide to long format so seaborn can plot multiple columns easily
                    df_melted = df_id.melt(
                        id_vars=['id', 'benchmark_type', 'environment'],
                        value_vars=available_metrics,
                        var_name='metric',
                        value_name='latency_ms'
                    )
                    
                    # Create the plot
                    plt.figure(figsize=(10, 6))
                    
                    # Boxplot automatically calculates percentiles and outliers
                    sns.boxplot(
                        data=df_melted, 
                        x='metric', 
                        y='latency_ms',
                        palette="Set2"
                    )
                    
                    # Beautify the plot
                    item_name = df_id['item_name_or_query'].iloc[0]
                    plt.title(f"Latency Distribution by Component\nEnvironment: {env} | Type: {btype}\nID: {item_id} ({item_name})", fontsize=14)
                    plt.xlabel("Component / Operation", fontsize=12)
                    plt.ylabel("Latency (ms)", fontsize=12)
                    plt.xticks(rotation=45, ha='right')
                    plt.tight_layout()
                    
                    # Save the current figure to the PDF
                    pdf.savefig()
                    plt.close()
                    
                    print(f"  Generated chart for ID: {item_id}, Type: {btype}")
                    
        print(f"Saved {output_pdf}")

if __name__ == '__main__':
    main()
