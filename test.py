import requests
import time
import os
import json

# URL của API (thay đổi nếu bạn triển khai trên Hugging Face)
# BASE_URL = "https://huynhtrungkiet09032005-pdf-translate-api.hf.space"  # hoặc URL Hugging Face của bạn
BASE_URL = "http://127.0.0.1:7860"  # hoặc URL Hugging Face của bạn

def test_pdf_translation_api(pdf_path): 
    print("=== BẮT ĐẦU KIỂM TRA API DỊCH PDF ===")
    
    # Kiểm tra trạng thái API
    try:
        print("\n1. Kiểm tra trạng thái API...")
        health_response = requests.get(f"{BASE_URL}/health")
        print(f"Kết quả: {health_response.status_code} - {health_response.text}")
    except Exception as e:
        print(f"Lỗi khi kiểm tra trạng thái API: {str(e)}")
        return

    # Kiểm tra danh sách ngôn ngữ và dịch vụ hỗ trợ
    try:
        print("\n2. Kiểm tra danh sách ngôn ngữ...")
        langs_response = requests.get(f"{BASE_URL}/languages")
        print(f"Kết quả: {langs_response.status_code}")
        
        print("\n3. Kiểm tra danh sách dịch vụ...")
        services_response = requests.get(f"{BASE_URL}/services")
        print(f"Kết quả: {services_response.status_code}")
    except Exception as e:
        print(f"Lỗi khi kiểm tra danh sách: {str(e)}")

    # Kiểm tra file tồn tại
    if not os.path.exists(pdf_path):
        print(f"\nLỗi: File {pdf_path} không tồn tại!")
        return
    
    # Tải lên file PDF để dịch
    print(f"\n4. Tải lên file PDF {pdf_path} để dịch...")
    try:
        with open(pdf_path, 'rb') as pdf_file:
            files = {'file': pdf_file}
            data = {
                'source_lang': 'en',
                'target_lang': 'vi',
                'service': 'google',
                'threads': '4'
            }
            
            translate_response = requests.post(f"{BASE_URL}/translate", files=files, data=data)
            print(f"Kết quả: {translate_response.status_code} - {translate_response.text}")
            
            if translate_response.status_code != 200:
                print("Lỗi khi tải lên file!")
                return
            
            translate_result = translate_response.json()
            task_id = translate_result['task_id']
            print(f"Task ID: {task_id}")
    except Exception as e:
        print(f"Lỗi khi tải lên file: {str(e)}")
        return

    # Kiểm tra trạng thái cho đến khi hoàn thành hoặc thất bại
    print("\n5. Theo dõi tiến trình dịch...")
    max_attempts = 60  # Tối đa 5 phút (5 * 60 = 300 giây)
    attempt = 0
    
    try:
        while attempt < max_attempts:
            status_response = requests.get(f"{BASE_URL}/translate/{task_id}/status")
            if status_response.status_code != 200:
                print(f"Lỗi kiểm tra trạng thái: {status_response.status_code} - {status_response.text}")
                break
                
            status = status_response.json()
            print(f"Tiến độ: {status['progress']}%, Trạng thái: {status['status']}")
            
            if status['status'] in ['completed', 'failed']:
                break
                
            time.sleep(5)  # Đợi 5 giây trước khi kiểm tra lại
            attempt += 1
            
        if attempt >= max_attempts:
            print("Đã hết thời gian chờ!")
            return
    except Exception as e:
        print(f"Lỗi khi kiểm tra trạng thái: {str(e)}")
        return

    # Nếu hoàn thành, tải xuống kết quả
    if status['status'] == 'completed':
        print("\n6. Tải xuống kết quả...")
        try:
            # Tạo tên file kết quả dựa trên tên file gốc
            base_filename = os.path.splitext(os.path.basename(pdf_path))[0]
            
            # Tải xuống bản song ngữ
            download_response = requests.get(f"{BASE_URL}/translate/{task_id}/download?type=dual")
            dual_filename = f"{base_filename}_en_vi.pdf"
            
            with open(dual_filename, 'wb') as f:
                f.write(download_response.content)
            print(f"Đã tải xuống file kết quả song ngữ: {dual_filename}")
            
            # Tải xuống bản đơn ngữ (chỉ tiếng Việt)
            download_mono_response = requests.get(f"{BASE_URL}/translate/{task_id}/download?type=mono")
            mono_filename = f"{base_filename}_vi.pdf"
            
            with open(mono_filename, 'wb') as f:
                f.write(download_mono_response.content)
            print(f"Đã tải xuống file kết quả tiếng Việt: {mono_filename}")
        except Exception as e:
            print(f"Lỗi khi tải xuống kết quả: {str(e)}")
    else:
        print(f"Dịch thất bại: {status.get('error', 'Không rõ lỗi')}")

    # Xóa task khi đã hoàn thành
    print("\n7. Dọn dẹp tài nguyên...")
    try:
        cleanup_response = requests.delete(f"{BASE_URL}/cleanup-task/{task_id}")
        print(f"Mã trạng thái: {cleanup_response.status_code}")
        
        try:
            cleanup_json = cleanup_response.json()
            print(f"Kết quả dọn dẹp: {json.dumps(cleanup_json, ensure_ascii=False)}")
        except requests.exceptions.JSONDecodeError:
            print(f"Phản hồi không phải JSON: {cleanup_response.text}")
    except Exception as e:
        print(f"Lỗi khi dọn dẹp: {str(e)}")

    print("\n=== KẾT THÚC KIỂM TRA API ===")

def test_extract_text_api(pdf_path):
    """
    Hàm kiểm tra API trích xuất văn bản với bounding boxes
    
    Args:
        pdf_path: Đường dẫn đến file PDF cần trích xuất văn bản
    """
    print("=== BẮT ĐẦU KIỂM TRA API TRÍCH XUẤT VĂN BẢN ===")
    
    # Kiểm tra file tồn tại
    if not os.path.exists(pdf_path):
        print(f"\nLỗi: File {pdf_path} không tồn tại!")
        return
    
    # Tải lên file PDF để trích xuất văn bản
    print(f"\n1. Tải lên file PDF {pdf_path} để trích xuất văn bản...")
    try:
        with open(pdf_path, 'rb') as pdf_file:
            files = {'file': pdf_file}
            
            extract_response = requests.post(f"{BASE_URL}/extract-text", files=files)
            print(f"Mã trạng thái: {extract_response.status_code}")
            
            if extract_response.status_code != 200:
                print(f"Lỗi khi trích xuất văn bản: {extract_response.text}")
                return
            
            # Lấy kết quả JSON
            result = extract_response.json()
            
            # Hiển thị thông tin về số trang và số đoạn văn bản
            print("\n2. Kết quả trích xuất:")
            page_count = len(result.get('pages', []))
            
            print(f"- Tổng số trang: {page_count}")
            
            total_chunks = 0
            for page_idx, page in enumerate(result.get('pages', []), 1):
                chunks = page.get('chunks', [])
                page_chunks = len(chunks)
                total_chunks += page_chunks
                print(f"- Trang {page_idx}: {page_chunks} đoạn văn bản")
                
                # Hiển thị một số đoạn văn bản ví dụ (tối đa 3 đoạn)
                if page_chunks > 0:
                    print("\n  Ví dụ một số đoạn văn bản:")
                    for i, chunk in enumerate(chunks[:3], 1):
                        text = chunk.get('text', '').replace('\n', ' ')[:50]  # Lấy 50 ký tự đầu tiên
                        box = chunk.get('box', [])
                        print(f"    + Đoạn {i}: '{text}...' - Box: {box}")
            
            print(f"\n- Tổng số đoạn văn bản: {total_chunks}")
            
    except Exception as e:
        print(f"Lỗi khi trích xuất văn bản: {str(e)}")
    
    print("\n=== KẾT THÚC KIỂM TRA API TRÍCH XUẤT VĂN BẢN ===")

def test_translation_prompt_api(pdf_path):
    """
    Hàm kiểm tra API dịch với chức năng prompt tùy chỉnh
    
    Args:
        pdf_path: Đường dẫn đến file PDF cần dịch
    """
    print("=== BẮT ĐẦU KIỂM TRA API DỊCH VỚI PROMPT TÙY CHỈNH ===")
    
    # Kiểm tra file tồn tại
    if not os.path.exists(pdf_path):
        print(f"\nLỗi: File {pdf_path} không tồn tại!")
        return
    
    # Tạo một prompt hướng dẫn dịch tương thích với test case
    prompt_translation = "Hãy dịch văn bản theo phong cách trang trọng như hợp đồng pháp lý. Giữ các thuật ngữ chuyên ngành trong ngoặc đơn."
    
    # Tải lên file PDF để dịch với prompt tùy chỉnh
    print(f"\n1. Tải lên file PDF {pdf_path} để dịch với prompt tùy chỉnh...")
    try:
        with open(pdf_path, 'rb') as pdf_file:
            files = {'file': pdf_file}
            data = {
                'source_lang': 'en',
                'target_lang': 'vi',
                'service': 'google',
                'threads': '4',
                'prompt_translation': prompt_translation
            }
            
            translate_response = requests.post(f"{BASE_URL}/translate", files=files, data=data)
            print(f"Kết quả: {translate_response.status_code} - {translate_response.text}")
            
            if translate_response.status_code != 200:
                print("Lỗi khi tải lên file!")
                return
            
            translate_result = translate_response.json()
            task_id = translate_result['task_id']
            print(f"Task ID: {task_id}")
            print(f"Sử dụng prompt: '{prompt_translation}'")
    except Exception as e:
        print(f"Lỗi khi tải lên file: {str(e)}")
        return

    # Kiểm tra trạng thái cho đến khi hoàn thành hoặc thất bại
    print("\n2. Theo dõi tiến trình dịch...")
    max_attempts = 60  # Tối đa 5 phút (5 * 60 = 300 giây)
    attempt = 0
    
    try:
        while attempt < max_attempts:
            status_response = requests.get(f"{BASE_URL}/translate/{task_id}/status")
            if status_response.status_code != 200:
                print(f"Lỗi kiểm tra trạng thái: {status_response.status_code} - {status_response.text}")
                break
                
            status = status_response.json()
            print(f"Tiến độ: {status['progress']}%, Trạng thái: {status['status']}")
            
            if status['status'] in ['completed', 'failed']:
                break
                
            time.sleep(5)  # Đợi 5 giây trước khi kiểm tra lại
            attempt += 1
            
        if attempt >= max_attempts:
            print("Đã hết thời gian chờ!")
            return
    except Exception as e:
        print(f"Lỗi khi kiểm tra trạng thái: {str(e)}")
        return

    # Nếu hoàn thành, tải xuống kết quả
    if status['status'] == 'completed':
        print("\n3. Tải xuống kết quả dịch với prompt tùy chỉnh...")
        try:
            # Tạo tên file kết quả dựa trên tên file gốc
            base_filename = os.path.splitext(os.path.basename(pdf_path))[0]
            
            # Tải xuống bản song ngữ
            download_response = requests.get(f"{BASE_URL}/translate/{task_id}/download?type=dual")
            dual_filename = f"{base_filename}_prompt_en_vi.pdf"
            
            with open(dual_filename, 'wb') as f:
                f.write(download_response.content)
            print(f"Đã tải xuống file kết quả song ngữ với prompt tùy chỉnh: {dual_filename}")
            
            # Tải xuống bản đơn ngữ (chỉ tiếng Việt)
            download_mono_response = requests.get(f"{BASE_URL}/translate/{task_id}/download?type=mono")
            mono_filename = f"{base_filename}_prompt_vi.pdf"
            
            with open(mono_filename, 'wb') as f:
                f.write(download_mono_response.content)
            print(f"Đã tải xuống file kết quả tiếng Việt với prompt tùy chỉnh: {mono_filename}")
            
            print("\n4. So sánh kết quả:")
            print(f"  + File dịch thông thường:         {base_filename}_vi.pdf")
            print(f"  + File dịch với prompt tùy chỉnh: {mono_filename}")
            print("\nHãy so sánh hai file để xem sự khác biệt về phong cách dịch!")
        except Exception as e:
            print(f"Lỗi khi tải xuống kết quả: {str(e)}")
    else:
        print(f"Dịch thất bại: {status.get('error', 'Không rõ lỗi')}")

    # Xóa task khi đã hoàn thành
    print("\n5. Dọn dẹp tài nguyên...")
    try:
        cleanup_response = requests.delete(f"{BASE_URL}/cleanup-task/{task_id}")
        print(f"Mã trạng thái: {cleanup_response.status_code}")
        
        try:
            cleanup_json = cleanup_response.json()
            print(f"Kết quả dọn dẹp: {json.dumps(cleanup_json, ensure_ascii=False)}")
        except requests.exceptions.JSONDecodeError:
            print(f"Phản hồi không phải JSON: {cleanup_response.text}")
    except Exception as e:
        print(f"Lỗi khi dọn dẹp: {str(e)}")

    print("\n=== KẾT THÚC KIỂM TRA API DỊCH VỚI PROMPT TÙY CHỈNH ===")

if __name__ == "__main__":
    # Thay đổi đường dẫn tới file PDF cần test
    pdf_file_path = "./test/file/translate.cli.text.with.figure.pdf"  # Thay đổi thành đường dẫn file PDF của bạn
    
    # Nếu chạy với tham số dòng lệnh
    import sys
    if len(sys.argv) > 1:
        pdf_file_path = sys.argv[1]
    
    # Thêm tham số dòng lệnh thứ hai để chọn loại test
    test_type = "all"
    if len(sys.argv) > 2:
        test_type = sys.argv[2]
    
    if test_type == "all" or test_type == "basic":
        test_pdf_translation_api(pdf_file_path)
    
    if test_type == "all" or test_type == "extract":
        test_extract_text_api(pdf_file_path)
    
    if test_type == "all" or test_type == "prompt":
        test_translation_prompt_api(pdf_file_path)