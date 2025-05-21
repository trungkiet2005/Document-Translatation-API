import json
from pathlib import Path
from threading import RLock  # Chuyển thành RLock
import os
import copy


class ConfigManager:
    _instance = None
    _lock = RLock()  # Sử dụng RLock thay cho Lock, cho phép lấy khóa nhiều lần trong cùng một luồng

    @classmethod
    def get_instance(cls):
        """Lấy instance singleton"""
        # Trước tiên kiểm tra xem instance đã tồn tại chưa, nếu chưa thì khóa và khởi tạo
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        # Ngăn chặn khởi tạo lặp lại
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True

        self._config_path = Path.home() / ".config" / "PDFMathTranslate" / "config.json"
        self._config_data = {}

        # Không cần thêm khóa ở đây, vì bên ngoài có thể đã khóa (get_instance), RLock cũng không sao
        self._ensure_config_exists()

    def _ensure_config_exists(self, isInit=True):
        """Đảm bảo file cấu hình tồn tại, nếu không thì tạo cấu hình mặc định"""
        # Ở đây cũng không cần khóa rõ ràng, lý do giống như trên, trong phần thân phương thức
        # gọi lại _load_config(), và _load_config() sẽ khóa bên trong. Vì RLock có thể vào lại, không bị chặn.
        if not self._config_path.exists():
            if isInit:
                self._config_path.parent.mkdir(parents=True, exist_ok=True)
                self._config_data = {}  # Nội dung cấu hình mặc định
                self._save_config()
            else:
                raise ValueError(f"config file {self._config_path} not found!")
        else:
            self._load_config()

    def _load_config(self):
        """Tải cấu hình từ config.json"""
        with self._lock:  # Khóa để đảm bảo an toàn đa luồng
            with self._config_path.open("r", encoding="utf-8") as f:
                self._config_data = json.load(f)

    def _save_config(self):
        """Lưu cấu hình vào config.json"""
        with self._lock:  # Khóa để đảm bảo an toàn đa luồng
            # Loại bỏ tham chiếu vòng tròn và ghi
            cleaned_data = self._remove_circular_references(self._config_data)
            with self._config_path.open("w", encoding="utf-8") as f:
                json.dump(cleaned_data, f, indent=4, ensure_ascii=False)

    def _remove_circular_references(self, obj, seen=None):
        """Đệ quy loại bỏ tham chiếu vòng tròn"""
        if seen is None:
            seen = set()
        obj_id = id(obj)
        if obj_id in seen:
            return None  # Khi gặp đối tượng đã xử lý, coi như tham chiếu vòng tròn
        seen.add(obj_id)

        if isinstance(obj, dict):
            return {
                k: self._remove_circular_references(v, seen) for k, v in obj.items()
            }
        elif isinstance(obj, list):
            return [self._remove_circular_references(i, seen) for i in obj]
        return obj

    @classmethod
    def custome_config(cls, file_path):
        """Sử dụng đường dẫn tùy chỉnh để tải file cấu hình"""
        custom_path = Path(file_path)
        if not custom_path.exists():
            raise ValueError(f"Config file {custom_path} not found!")
        # Khóa
        with cls._lock:
            instance = cls()
            instance._config_path = custom_path
            # Ở đây truyền isInit=False, nếu không tồn tại thì báo lỗi; nếu tồn tại thì _load_config() bình thường
            instance._ensure_config_exists(isInit=False)
            cls._instance = instance

    @classmethod
    def get(cls, key, default=None):
        """Lấy giá trị cấu hình"""
        instance = cls.get_instance()
        # Khi đọc, khóa hay không khóa đều được. Nhưng để thống nhất, chúng ta khóa trước và sau khi sửa đổi cấu hình.
        # get chỉ cần lưu cuối cùng, thì sẽ khóa -> _save_config()
        if key in instance._config_data:
            return instance._config_data[key]

        # Nếu key tồn tại trong biến môi trường, sử dụng biến môi trường và ghi lại vào config
        if key in os.environ:
            value = os.environ[key]
            instance._config_data[key] = value
            instance._save_config()
            return value

        # Nếu default không phải None, thì thiết lập và lưu
        if default is not None:
            instance._config_data[key] = default
            instance._save_config()
            return default

        # Không tìm thấy thì trả về ngoại lệ
        # raise KeyError(f"{key} is not found in config file or environment variables.")
        return default

    @classmethod
    def set(cls, key, value):
        """Thiết lập giá trị cấu hình và lưu"""
        instance = cls.get_instance()
        with instance._lock:
            instance._config_data[key] = value
            instance._save_config()

    @classmethod
    def get_translator_by_name(cls, name):
        """Lấy cấu hình translator tương ứng dựa trên name"""
        instance = cls.get_instance()
        translators = instance._config_data.get("translators", [])
        for translator in translators:
            if translator.get("name") == name:
                return translator["envs"]
        return None

    @classmethod
    def set_translator_by_name(cls, name, new_translator_envs):
        """Thiết lập hoặc cập nhật cấu hình translator dựa trên name"""
        instance = cls.get_instance()
        with instance._lock:
            translators = instance._config_data.get("translators", [])
            for translator in translators:
                if translator.get("name") == name:
                    translator["envs"] = copy.deepcopy(new_translator_envs)
                    instance._save_config()
                    return
            translators.append(
                {"name": name, "envs": copy.deepcopy(new_translator_envs)}
            )
            instance._config_data["translators"] = translators
            instance._save_config()

    @classmethod
    def get_env_by_translatername(cls, translater_name, name, default=None):
        """Lấy cấu hình translator tương ứng dựa trên name"""
        instance = cls.get_instance()
        translators = instance._config_data.get("translators", [])
        for translator in translators:
            if translator.get("name") == translater_name.name:
                if translator["envs"][name]:
                    return translator["envs"][name]
                else:
                    with instance._lock:
                        translator["envs"][name] = default
                        instance._save_config()
                        return default

        with instance._lock:
            translators = instance._config_data.get("translators", [])
            for translator in translators:
                if translator.get("name") == translater_name.name:
                    translator["envs"][name] = default
                    instance._save_config()
                    return default
            translators.append(
                {
                    "name": translater_name.name,
                    "envs": copy.deepcopy(translater_name.envs),
                }
            )
            instance._config_data["translators"] = translators
            instance._save_config()
            return default

    @classmethod
    def delete(cls, key):
        """Xóa giá trị cấu hình và lưu"""
        instance = cls.get_instance()
        with instance._lock:
            if key in instance._config_data:
                del instance._config_data[key]
                instance._save_config()

    @classmethod
    def clear(cls):
        """Xóa tất cả giá trị cấu hình và lưu"""
        instance = cls.get_instance()
        with instance._lock:
            instance._config_data = {}
            instance._save_config()

    @classmethod
    def all(cls):
        """Trả về tất cả các mục cấu hình"""
        instance = cls.get_instance()
        # Đây chỉ là thao tác đọc, thường không cần khóa. Tuy nhiên để an toàn cũng có thể khóa.
        return instance._config_data

    @classmethod
    def remove(cls):
        instance = cls.get_instance()
        with instance._lock:
            os.remove(instance._config_path)
