import os
import glob
import csv

def merge_csvs():
    input_dir = '../../results/version-3'
    output_dir = '../results'
    output_file = os.path.join(output_dir, 'merged.csv')
    
    os.makedirs(output_dir, exist_ok=True)
    csv_files = glob.glob(os.path.join(input_dir, '*.csv'))
    
    if not csv_files:
        print(f"No CSV files found in {input_dir}")
        return
    
    with open(output_file, 'w', newline='', encoding='utf-8') as outfile:
        writer = csv.writer(outfile)
        header_written = False
        
        for file in csv_files:
            try:
                with open(file, 'r', newline='', encoding='utf-8') as infile:
                    reader = csv.reader(infile)
                    try:
                        header = next(reader)
                    except StopIteration:
                        continue # empty file
                    
                    if not header_written:
                        writer.writerow(header)
                        header_written = True
                        
                    for row in reader:
                        writer.writerow(row)
                print(f"Merged {file}")
            except Exception as e:
                print(f"Error reading {file}: {e}")
                
    print(f"Successfully merged {len(csv_files)} files into {output_file}")

if __name__ == '__main__':
    merge_csvs()
