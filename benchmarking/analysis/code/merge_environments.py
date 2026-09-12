import csv
import os

def merge_envs():
    input_dir = '../merge'
    files = {
        'microvm': os.path.join(input_dir, 'microvm-merged.csv'),
        'lambda': os.path.join(input_dir, 'lambda-merged.csv')
    }
    output_file = os.path.join(input_dir, 'merged.csv')
    
    rows = []
    header = None
    
    for env, path in files.items():
        if not os.path.exists(path):
            print(f"File not found: {path}")
            continue
            
        with open(path, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            try:
                curr_header = next(reader)
                if header is None:
                    header = ['environment'] + curr_header
            except StopIteration:
                continue
                
            for row in reader:
                rows.append([env] + row)
                
    if header is not None:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        print(f"Successfully created {output_file} with {len(rows)} rows.")
    else:
        print("No data found to merge.")

if __name__ == '__main__':
    merge_envs()
