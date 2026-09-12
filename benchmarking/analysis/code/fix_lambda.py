import re

def fix_csv():
    filepath = '../merge/lambda-merged.csv'
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()
        
    # Replace quotes around numbers with commas, e.g., "773,03" -> 773.03
    fixed_text = re.sub(r'"(\d+),(\d+)"', r'\1.\2', text)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(fixed_text)
        
if __name__ == '__main__':
    fix_csv()
