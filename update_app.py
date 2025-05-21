import re

def update_imports_in_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Replace imports
        updated = re.sub(r'from pdf2zh\.', 'from code_pdf.', content)
        updated = re.sub(r'import pdf2zh\.', 'import code_pdf.', updated)
        
        # Update cache paths
        updated = re.sub(r'os.makedirs\("/tmp/\.cache/pdf2zh', 'os.makedirs("/tmp/.cache/code_pdf', updated)
        
        if content != updated:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(updated)
            print(f"Updated {file_path}")
            return True
        return False
    except Exception as e:
        print(f"Error updating {file_path}: {str(e)}")
        return False

# Update app.py
if update_imports_in_file('app.py'):
    print("app.py updated successfully")
else:
    print("No changes needed in app.py") 