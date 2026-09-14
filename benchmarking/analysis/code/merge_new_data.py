import os
import glob
import csv
import re

def fix_decimals(text):
    # Fix comma decimals like "773,03" -> 773.03
    return re.sub(r'"(\d+),(\d+)"', r'\1.\2', text)

def merge_new_data():
    base_results = '../../results'
    merge_dir = '../merge'
    
    envs = ['lambda', 'microvm']
    
    # We want consistent IDs across all data
    item_to_id = {}
    current_id = 1
    
    os.makedirs(merge_dir, exist_ok=True)
    
    for env in envs:
        env_dir = os.path.join(base_results, env)
        if not os.path.exists(env_dir):
            print(f"Directory not found: {env_dir}")
            continue
            
        csv_files = glob.glob(os.path.join(env_dir, '*.csv'))
        
        query_rows = []
        ingest_rows = []
        query_header = None
        ingest_header = None
        
        for file in csv_files:
            # Read text and fix commas
            with open(file, 'r', encoding='utf-8') as f:
                content = f.read()
            content = fix_decimals(content)
            
            # Parse as CSV
            lines = content.splitlines()
            if not len(lines) > 1:
                continue
                
            reader = csv.reader(lines)
            try:
                header = next(reader)
            except StopIteration:
                continue
            
            # Identify the item name column
            item_col_idx = -1
            for col_name in ['item_name_or_query', 'query', 'doc_name']:
                if col_name in header:
                    item_col_idx = header.index(col_name)
                    break
                    
            if item_col_idx == -1:
                print(f"Skipping {file} due to missing item name column. Header: {header}")
                continue
                
            # Determine if query or ingest based on filename or benchmark_type
            is_query = False
            is_ingest = False
            
            btype_idx = header.index('benchmark_type') if 'benchmark_type' in header else -1
            
            # First try looking at the filename
            basename = os.path.basename(file).lower()
            if 'query' in basename:
                is_query = True
            elif 'ingest' in basename:
                is_ingest = True
            elif btype_idx != -1:
                # Fallback to column
                btype = lines[1].split(',')[btype_idx].lower()
                if 'query' in btype:
                    is_query = True
                elif 'ingest' in btype:
                    is_ingest = True
                    
            if not is_query and not is_ingest:
                print(f"Could not determine if {file} is query or ingest.")
                continue
                
            for row in reader:
                if not row or len(row) != len(header):
                    continue
                
                item_val = row[item_col_idx]
                if item_val not in item_to_id:
                    item_to_id[item_val] = current_id
                    current_id += 1
                
                out_row = [item_to_id[item_val]] + row
                out_header = ['id'] + header
                
                if is_query:
                    if query_header is None:
                        query_header = out_header
                    query_rows.append(out_row)
                else:
                    if ingest_header is None:
                        ingest_header = out_header
                    ingest_rows.append(out_row)
        
        # Write out the CSVs for this env
        query_out = os.path.join(merge_dir, f"{env}_query_merged.csv")
        if query_rows and query_header:
            with open(query_out, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(query_header)
                writer.writerows(query_rows)
            print(f"Saved {len(query_rows)} rows to {query_out}")
            
        ingest_out = os.path.join(merge_dir, f"{env}_ingest_merged.csv")
        if ingest_rows and ingest_header:
            with open(ingest_out, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(ingest_header)
                writer.writerows(ingest_rows)
            print(f"Saved {len(ingest_rows)} rows to {ingest_out}")

if __name__ == '__main__':
    merge_new_data()
