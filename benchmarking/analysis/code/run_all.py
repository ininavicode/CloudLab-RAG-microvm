import os
import sys
import subprocess

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    analysis_dir = os.path.abspath(os.path.join(script_dir, '..'))
    
    # 1. Run merge_new_data.py
    merge_script = os.path.join(script_dir, 'merge_new_data.py')
    print("=" * 60)
    print(f"Executing Data Merge: {os.path.basename(merge_script)}")
    print("=" * 60)
    
    if os.path.exists(merge_script):
        result = subprocess.run([sys.executable, merge_script], cwd=script_dir)
        if result.returncode != 0:
            print("Error: merge_new_data.py failed. Aborting further execution.")
            sys.exit(1)
    else:
        print(f"Error: {merge_script} not found.")
        sys.exit(1)
        
    print("\nData merge completed successfully.\n")
    
    # 2. Dynamically find and run all analysis scripts
    # Exclude these utility/data directories
    exclude_dirs = {'.venv', 'code', 'merge', 'results', '__pycache__'}
    
    analysis_scripts = []
    
    # Iterate over directories in analysis_dir
    for item in os.listdir(analysis_dir):
        item_path = os.path.join(analysis_dir, item)
        
        # Check if it's a directory and not excluded or hidden
        if os.path.isdir(item_path) and item not in exclude_dirs and not item.startswith('.'):
            # Find python files inside
            for file in os.listdir(item_path):
                if file.endswith('.py'):
                    analysis_scripts.append({
                        'name': file,
                        'path': os.path.join(item_path, file),
                        'dir': item_path
                    })
                    
    # Sort scripts by name to ensure consistent execution order
    analysis_scripts.sort(key=lambda x: x['name'])
                    
    if not analysis_scripts:
        print("No analysis scripts found to execute.")
        return
        
    print(f"Found {len(analysis_scripts)} analysis script(s) to execute:\n")
    for script in analysis_scripts:
        print(f" - {script['name']} (in {os.path.basename(script['dir'])})")
    
    print("\nStarting execution...\n")
    
    for script in analysis_scripts:
        print("=" * 60)
        print(f"Executing Analysis: {script['name']}")
        print("=" * 60)
        
        # Execute the script dynamically, using its directory as the working directory
        result = subprocess.run([sys.executable, script['name']], cwd=script['dir'])
        
        if result.returncode != 0:
            print(f"Warning: {script['name']} exited with code {result.returncode}")
        else:
            print(f"Successfully completed: {script['name']}\n")
            
    print("=" * 60)
    print("All tasks finished successfully.")

if __name__ == '__main__':
    main()
