import os
import csv

def add_id_column():
    input_file = '../results/merged.csv'
    output_file = '../results/merged_with_id.csv'
    
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return

    # Dictionary to hold the mapping from item_name_or_query to an ID
    item_to_id = {}
    current_id = 1
    
    with open(input_file, 'r', newline='', encoding='utf-8') as infile:
        reader = csv.reader(infile)
        try:
            header = next(reader)
        except StopIteration:
            print("Empty CSV")
            return
        
        try:
            item_idx = header.index('item_name_or_query')
        except ValueError:
            print("Column 'item_name_or_query' not found in header.")
            return

        header.insert(0, 'id')
        
        rows = []
        for row in reader:
            if not row:
                continue
            item_val = row[item_idx]
            if item_val not in item_to_id:
                item_to_id[item_val] = current_id
                current_id += 1
            
            row.insert(0, item_to_id[item_val])
            rows.append(row)
            
    with open(input_file, 'w', newline='', encoding='utf-8') as outfile:
        writer = csv.writer(outfile)
        writer.writerow(header)
        writer.writerows(rows)
        
    print(f"Added 'id' column to {input_file}. Total unique items: {current_id - 1}")

if __name__ == '__main__':
    add_id_column()
