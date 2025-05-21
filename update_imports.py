import os
import re

def update_imports_in_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Replace imports
        updated = re.sub(r'from pdf2zh\.', 'from code_pdf.', content)
        updated = re.sub(r'import pdf2zh\.', 'import code_pdf.', updated)
        
        # Update docstrings
        updated = re.sub(r'pdf2zh\.six', 'code_pdf.six', updated)
        
        # Update cache paths
        updated = re.sub(r'\.cache", "pdf2zh"', '.cache", "code_pdf"', updated)
        updated = re.sub(r'\.cache/pdf2zh', '.cache/code_pdf', updated)
        
        if content != updated:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(updated)
            print(f"Updated {file_path}")
            return True
        return False
    except Exception as e:
        print(f"Error updating {file_path}: {str(e)}")
        return False

# Update files in code_pdf directory
code_pdf_dir = 'code_pdf'
count = 0
for filename in os.listdir(code_pdf_dir):
    if filename.endswith('.py'):
        file_path = os.path.join(code_pdf_dir, filename)
        if update_imports_in_file(file_path):
            count += 1

# Update app.py
if os.path.exists('app.py'):
    if update_imports_in_file('app.py'):
        count += 1

print(f"Total files updated: {count}") 