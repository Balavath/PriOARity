# prioarity_complete.py
import os
import json
import re
import shutil
import msgpack
import FreeSimpleGUI as sg
from datetime import datetime

# ==== Config / constants ====
OAR_KEYWORD = "OpenAnimationReplacer"
DAR_KEYWORD = "DynamicAnimationReplacer"
LOG_ENCODING = "utf-8"

# Типы анимационных модов
class ModType:
    OAR = "OAR"
    DAR_LEGACY_CUSTOM = "DAR Legacy (Custom)"
    DAR_LEGACY_ACTOR = "DAR Legacy (Actor)"
    MIXED = "Mixed"

# ==== Shared helpers ====

def is_oar_root_path(path_str):
    if not path_str:
        return False
    return OAR_KEYWORD.lower() in path_str.replace("/", "\\").lower()

def is_dar_legacy_path(path_str):
    """Проверяет, является ли путь частью DAR Legacy структуры."""
    if not path_str:
        return False
    path_lower = path_str.replace("/", "\\").lower()
    return DAR_KEYWORD.lower() in path_lower

def parse_conditions_txt(conditions_file):
    """
    Парсит _conditions.txt файл из DAR Legacy мода.
    Возвращает строку с условиями или None если файл не найден.
    Формат: IsEquippedRight("Skyrim.esm" | 0x0001397E) AND NOT IsInInterior()
    """
    if not os.path.exists(conditions_file):
        return None
    
    try:
        with open(conditions_file, "r", encoding=LOG_ENCODING) as f:
            lines = []
            for line in f:
                line = line.strip()
                # Пропускаем комментарии и пустые строки
                if line and not line.startswith(";"):
                    lines.append(line)
            return " ".join(lines) if lines else None
    except Exception:
        return None

def scan_dar_legacy_structure(mod_path):
    """
    Сканирует мод на наличие DAR Legacy структуры.
    
    Возвращает список кортежей:
    - (priority, condition_string, hkx_files_count, folder_type)
    
    folder_type: "custom" для _CustomConditions/<priority>/
                 "actor" для <Mod.esp>/<FormID>/
    """
    results = []
    
    # Поиск папок meshes/actors/*/animations/DynamicAnimationReplacer
    dar_base = os.path.join(mod_path, "meshes", "actors")
    if not os.path.isdir(dar_base):
        return results
    
    for root, dirs, files in os.walk(dar_base):
        if DAR_KEYWORD.lower() not in root.replace("/", "\\").lower():
            continue
        
        # Проверка на _CustomConditions
        if "_CustomConditions" in root:
            # Имя текущей папки должно быть приоритетом
            current_folder = os.path.basename(root)
            
            # Пропускаем саму папку _CustomConditions
            if current_folder.lower() == "_customconditions":
                continue
            
            # Пытаемся извлечь приоритет из имени папки
            try:
                priority = int(current_folder)
                if priority == 0:
                    continue  # 0 невалиден для CustomConditions
                
                # Конвертируем в знаковое 32-битное число (как в OAR)
                if priority > 2147483647:
                    priority = priority - 4294967296  # конвертация unsigned -> signed
            except ValueError:
                continue
            
            # Парсинг _conditions.txt
            conditions_file = os.path.join(root, "_conditions.txt")
            condition = parse_conditions_txt(conditions_file)
            
            # Сбор .hkx файлов
            hkx_files = [f for f in files if f.lower().endswith(".hkx")]
            
            if hkx_files:
                results.append((priority, condition, len(hkx_files), "custom"))
        
        else:
            # ActorBase структура: .../DynamicAnimationReplacer/<Mod.esp>/<FormID>/
            # priority = 0
            rel_path = os.path.relpath(root, dar_base)
            parts = rel_path.replace("/", "\\").split("\\")
            
            # Ожидаем структуру: <project>/animations/DynamicAnimationReplacer/<Mod.esp>/<FormID>
            if len(parts) >= 5 and parts[2].lower() == DAR_KEYWORD.lower():
                mod_name = parts[-2]  # например, Skyrim.esm
                form_id = parts[-1]    # например, 00000007
                
                # Проверка, что form_id — 8-значный hex
                if len(form_id) == 8 and all(c in "0123456789ABCDEFabcdef" for c in form_id):
                    condition = f'IsActorBase("{mod_name}"|{form_id})'
                    hkx_files = [f for f in files if f.lower().endswith(".hkx")]
                    
                    if hkx_files:
                        results.append((0, condition, len(hkx_files), "actor"))
    
    return results

def detect_mod_type(mod_path):
    """
    Определяет тип мода: OAR, DAR Legacy или смешанный.
    Возвращает ModType.*
    """
    has_oar = is_oar_mod(mod_path)
    
    dar_legacy_results = scan_dar_legacy_structure(mod_path)
    has_dar = len(dar_legacy_results) > 0
    
    if has_oar and has_dar:
        return ModType.MIXED
    elif has_oar:
        return ModType.OAR
    elif has_dar:
        # Определяем тип DAR Legacy
        has_custom = any(r[3] == "custom" for r in dar_legacy_results)
        has_actor = any(r[3] == "actor" for r in dar_legacy_results)
        
        if has_custom and has_actor:
            return ModType.MIXED
        elif has_custom:
            return ModType.DAR_LEGACY_CUSTOM
        else:
            return ModType.DAR_LEGACY_ACTOR
    
    return None

def is_oar_mod(mod_path):
    """Проверяет, содержит ли мод OAR-анимации."""
    try:
        for root, dirs, _ in os.walk(mod_path):
            if OAR_KEYWORD in root or OAR_KEYWORD in " ".join(dirs):
                return True
    except Exception:
        return False
    return False

def collect_jsons(mod_path):
    """
    Собирает JSON-файлы внутри OAR-папок.
    Возвращает список (src_file, rel_path, filename, data_dict, old_priority).
    Пропускает json'ы без поля 'priority'.
    """
    entries = []
    for root, _, files in os.walk(mod_path):
        if OAR_KEYWORD.lower() not in root.replace("/", "\\").lower():
            continue
        rel_path = os.path.relpath(root, mod_path)
        for file in files:
            if not file.lower().endswith(".json"):
                continue
            src_file = os.path.join(root, file)
            try:
                with open(src_file, encoding=LOG_ENCODING) as f:
                    data = json.load(f)
                if "priority" not in data:
                    # skip meta jsons without priority
                    continue
                old_pri = data["priority"]
            except Exception as e:
                # bubble up so caller can decide to skip mod or notify user
                raise RuntimeError(f"Read error {src_file}: {e}")
            entries.append((src_file, rel_path, file, data, old_pri))
    # sort by old priority to keep stable ordering when rewriting
    entries.sort(key=lambda x: x[4])
    return entries

def collect_dar_legacy_entries(mod_path):
    """
    Собирает DAR Legacy записи из структуры мода.
    Возвращает список виртуальных entries:
    (virtual_src, rel_path, filename, data_dict, priority, condition, hkx_count, entry_type)
    
    entry_type: "custom" для _CustomConditions, "actor" для ActorBase
    """
    entries = []
    dar_results = scan_dar_legacy_structure(mod_path)
    
    for priority, condition, hkx_count, entry_type in dar_results:
        # Создаём виртуальную запись
        if entry_type == "custom":
            # Для CustomConditions создаём структуру с приоритетом
            rel_path = f"meshes\\actors\\character\\animations\\{DAR_KEYWORD}\\_CustomConditions\\{priority}"
            filename = f"dar_config_priority_{priority}.json"
        else:  # actor
            # Извлекаем mod name и form_id из condition
            # Формат: IsActorBase("ModName.esp"|FormID)
            import re
            match = re.search(r'IsActorBase\("([^"]+)"\|([0-9A-Fa-f]+)\)', condition or "")
            if match:
                mod_name = match.group(1)
                form_id = match.group(2)
                rel_path = f"meshes\\actors\\character\\animations\\{DAR_KEYWORD}\\{mod_name}\\{form_id}"
                filename = f"dar_config_actorbase_{form_id}.json"
            else:
                rel_path = f"meshes\\actors\\character\\animations\\{DAR_KEYWORD}\\actorbase_{priority}"
                filename = f"dar_config_actorbase_{priority}.json"
        
        # Создаём виртуальный data dict
        data = {
            "priority": priority,
            "dar_legacy": True,
            "dar_type": entry_type,
            "hkx_count": hkx_count
        }
        if condition:
            data["condition"] = condition
        
        # virtual_src = None (так как это виртуальная запись)
        entries.append((None, rel_path, filename, data, priority, condition, hkx_count, entry_type))
    
    # Сортируем по приоритету
    entries.sort(key=lambda x: x[4])
    return entries

def get_mod_animation_entries(mod_path):
    """
    Получает все анимационные записи из мода (OAR + DAR Legacy).
    Возвращает (oar_entries, dar_entries, mod_type)
    """
    mod_type = detect_mod_type(mod_path)
    oar_entries = collect_jsons(mod_path) if mod_type in (ModType.OAR, ModType.MIXED) else []
    dar_entries = collect_dar_legacy_entries(mod_path) if mod_type in (ModType.DAR_LEGACY_CUSTOM, ModType.DAR_LEGACY_ACTOR, ModType.MIXED) else []
    return oar_entries, dar_entries, mod_type

def copy_dar_legacy_mod(mod_folder_path, out_dir, mod_display_name, start_priority, log_lines):
    """
    Создаёт структуру папок и user.json для DAR Legacy мода с новыми приоритетами.
    Не копирует .hkx файлы — OAR прочитает их из оригинального мода через VFS.
    
    Для CustomConditions:
    - Создаёт структуру папок с новым приоритетом
    - Создаёт user.json с новым priority
    
    Для ActorBase:
    - Создаёт структуру <Mod.esp>/<FormID>/
    - Создаёт user.json с priority = 0
    """
    priority_counter = start_priority
    dar_base = os.path.join(mod_folder_path, "meshes", "actors")
    
    if not os.path.isdir(dar_base):
        return priority_counter
    
    for root, dirs, files in os.walk(dar_base):
        if DAR_KEYWORD.lower() not in root.replace("/", "\\").lower():
            continue
        
        # Проверка на _CustomConditions
        if "_CustomConditions" in root:
            current_folder = os.path.basename(root)
            
            if current_folder.lower() == "_customconditions":
                continue
            
            try:
                old_priority = int(current_folder)
                if old_priority == 0:
                    continue
            except ValueError:
                continue
            
            # Создаём структуру с НОВЫМ приоритетом
            new_priority_path = os.path.join(out_dir, "meshes", "actors", "character", "animations", 
                                            DAR_KEYWORD, "_CustomConditions", str(priority_counter))
            os.makedirs(new_priority_path, exist_ok=True)
            
            # Создаём user.json с новым приоритетом
            user_json = {
                "priority": priority_counter,
                "disabled": False,
                "replacementAnimations": []  # OAR возьмёт анимации из оригинального мода
            }
            
            # Проверяем _conditions.txt в оригинале
            conditions_src = os.path.join(root, "_conditions.txt")
            if os.path.exists(conditions_src):
                # Копируем _conditions.txt в новую папку
                conditions_dst = os.path.join(new_priority_path, "_conditions.txt")
                try:
                    shutil.copy2(conditions_src, conditions_dst)
                    user_json["conditions"] = [{"condition": "loaded_from_conditions_txt"}]
                except Exception as e:
                    log_lines.append(f"  Warning: Failed to copy _conditions.txt: {e}")
            
            user_json_path = os.path.join(new_priority_path, "user.json")
            with open(user_json_path, "w", encoding=LOG_ENCODING) as f:
                json.dump(user_json, f, ensure_ascii=False, indent=2)
            
            log_lines.append(f"[{mod_display_name}] DAR Custom: priority {old_priority} → {priority_counter}")
            priority_counter += 1
        
        else:
            # ActorBase структура
            rel_path = os.path.relpath(root, dar_base)
            parts = rel_path.replace("/", "\\").split("\\")
            
            if len(parts) >= 5 and parts[2].lower() == DAR_KEYWORD.lower():
                mod_name = parts[-2]
                form_id = parts[-1]
                
                if len(form_id) == 8 and all(c in "0123456789ABCDEFabcdef" for c in form_id):
                    # Копируем структуру (priority = 0 для ActorBase)
                    target_path = os.path.join(out_dir, "meshes", "actors", "character", "animations",
                                              DAR_KEYWORD, mod_name, form_id)
                    os.makedirs(target_path, exist_ok=True)
                    
                    # Создаём user.json с priority = 0
                    user_json = {
                        "priority": 0,
                        "disabled": False,
                        "replacementAnimations": []
                    }
                    
                    user_json_path = os.path.join(target_path, "user.json")
                    with open(user_json_path, "w", encoding=LOG_ENCODING) as f:
                        json.dump(user_json, f, ensure_ascii=False, indent=2)
                    
                    log_lines.append(f"[{mod_display_name}] DAR ActorBase: {mod_name}\\{form_id} (priority 0)")
    
    return priority_counter

def copy_jsons_from_mod(mod_folder_path, out_dir, mod_display_name, priority_counter, log_lines, include_dar_legacy=False):
    """
    Копирование json'ов и назначение новых priority.
    Возвращает обновлённый priority_counter.

    Если include_dar_legacy=True, также обрабатывает DAR Legacy моды.
    """
    # Обрабатываем OAR записи
    jsons = collect_jsons(mod_folder_path)
    for src_file, rel_path, file, data, old_pri in jsons:
        target_root = os.path.join(out_dir, rel_path)
        os.makedirs(target_root, exist_ok=True)
        dst_file = os.path.join(target_root, file)
        if "priority" in data:
            data["priority"] = priority_counter
            log_lines.append(f"[{mod_display_name}] {src_file} : {old_pri} → {priority_counter}")
            priority_counter += 1
        with open(dst_file, "w", encoding=LOG_ENCODING) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # Обрабатываем DAR Legacy записи если включено
    if include_dar_legacy:
        priority_counter = copy_dar_legacy_mod(mod_folder_path, out_dir, mod_display_name, priority_counter, log_lines)

    return priority_counter

def find_priority_conflicts(mods_dir, selected_mods_ordered, include_dar_legacy=False):
    """
    Проверка на дубли приоритетов между выбранными модами (folders).
    selected_mods_ordered: список имён папок (в mods/staging).
    include_dar_legacy: если True, также проверяет DAR Legacy моды.
    Возвращает duplicate_conflicts: [(priority, [folders])]
    """
    all_entries = []
    for load_index, mod in enumerate(selected_mods_ordered):
        mod_path = os.path.join(mods_dir, mod)
        if not os.path.exists(mod_path):
            continue
        
        # OAR записи
        try:
            jsons = collect_jsons(mod_path)
        except RuntimeError:
            jsons = []
        
        for _, _, file_name, data, priority in jsons:
            try:
                pri_val = int(priority)
            except Exception:
                pri_val = priority
            all_entries.append((mod, file_name, pri_val, load_index, "OAR"))
        
        # DAR Legacy записи
        if include_dar_legacy:
            dar_entries = collect_dar_legacy_entries(mod_path)
            for _, rel_path, filename, data, priority, condition, hkx_count, entry_type in dar_entries:
                try:
                    pri_val = int(priority)
                except Exception:
                    pri_val = priority
                all_entries.append((mod, filename, pri_val, load_index, f"DAR-{entry_type}"))

    pri_map = {}
    for mod, file_name, pri, _, _ in all_entries:
        pri_map.setdefault(pri, set()).add(mod)

    duplicate_conflicts = [(pri, sorted(list(folders))) for pri, folders in pri_map.items() if len(folders) > 1]
    return duplicate_conflicts

# ==== Vortex helpers ====

def recursive_find_entries(obj):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and ('relPath' in obj[0] or 'relpath' in obj[0]):
            return obj
        for item in obj:
            res = recursive_find_entries(item)
            if res:
                return res
    elif isinstance(obj, dict):
        for v in obj.values():
            res = recursive_find_entries(v)
            if res:
                return res
    return None

def recursive_find_key(obj, key_name):
    if isinstance(obj, dict):
        if key_name in obj:
            return obj[key_name]
        for v in obj.values():
            res = recursive_find_key(v, key_name)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = recursive_find_key(item, key_name)
            if res is not None:
                return res
    return None

def load_vortex_deployment(deployment_file):
    if not os.path.exists(deployment_file):
        raise FileNotFoundError(f"Deployment file not found: {deployment_file}")
    with open(deployment_file, "rb") as f:
        data = msgpack.unpack(f, raw=False)
    entries = recursive_find_entries(data)
    if entries is None:
        entries = data.get("files") if isinstance(data, dict) else None
    if entries is None:
        entries = data.get("entries") if isinstance(data, dict) else None
    staging_path = recursive_find_key(data, "stagingPath")
    target_path = recursive_find_key(data, "targetPath")
    return {"stagingPath": staging_path, "targetPath": target_path, "entries": entries or []}

def extract_ordered_sources_from_entries(entries):
    seen = []
    for e in entries:
        rel = e.get("relPath") or e.get("relpath") or ""
        src = e.get("source") or e.get("Source") or e.get("mod") or None
        if not src:
            continue
        if OAR_KEYWORD.lower() in str(rel).lower() or DAR_KEYWORD.lower() in str(rel).lower():
            if src not in seen:
                seen.append(src)
    return seen

def canonicalize_name(s):
    s = s or ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def find_mod_folder_by_source(staging_mods_dir, source_name):
    if not os.path.isdir(staging_mods_dir):
        return None
    try:
        candidates = [d for d in os.listdir(staging_mods_dir) if os.path.isdir(os.path.join(staging_mods_dir, d))]
    except Exception:
        return None
    src_can = canonicalize_name(source_name)
    for c in candidates:
        if canonicalize_name(c) == src_can:
            return c
    for c in candidates:
        cand_can = canonicalize_name(c)
        if cand_can in src_can or src_can in cand_can:
            return c
    src_base = source_name.split("-")[0].strip()
    src_base_can = canonicalize_name(src_base)
    for c in candidates:
        if src_base_can and src_base_can in canonicalize_name(c):
            return c
    return None

def manual_order_window(selected_sources):
    layout = [
        [sg.Text("Manual reordering of selected mods:", font=("Default", 12, "bold"))],
        [
            sg.Listbox(
                values=selected_sources,
                size=(80, 25),
                key="LIST",
                select_mode=sg.LISTBOX_SELECT_MODE_SINGLE,
                enable_events=True
            ),
            sg.Column([
                [sg.Button("↑ Up", size=(10,1))],
                [sg.Button("↓ Down", size=(10,1))],
                [sg.Button("⏫ Top", size=(10,1))],
                [sg.Button("⏬ Bottom", size=(10,1))],
                [sg.Text("")],
                [sg.Button("OK", button_color=("white", "green"), size=(10,1))],
                [sg.Button("Cancel", size=(10,1))]
            ], element_justification="center", vertical_alignment="top", pad=(10,0))
        ]
    ]

    win = sg.Window("Manual order", layout, modal=True, resizable=True)

    mods = selected_sources[:]
    while True:
        event, values = win.read()
        if event in (sg.WIN_CLOSED, "Cancel"):
            mods = None
            break
        idxs = values.get("LIST", [])
        if not idxs:
            continue
        cur = idxs[0]
        i = mods.index(cur)

        if event == "↑ Up" and i > 0:
            mods[i-1], mods[i] = mods[i], mods[i-1]
        elif event == "↓ Down" and i < len(mods)-1:
            mods[i+1], mods[i] = mods[i], mods[i+1]
        elif event == "⏫ Top":
            mods.insert(0, mods.pop(i))
        elif event == "⏬ Bottom":
            mods.append(mods.pop(i))
        elif event == "OK":
            break

        win["LIST"].update(mods, set_to_index=[mods.index(cur)])
    win.close()
    return mods


# ==== UI helpers ====

def append_log(window, text):
    print(text)
    try:
        cur = window["LOG"].get() if window and window["LOG"] else ""
    except Exception:
        cur = ""
    new = (cur + ("\n" if cur else "") + text).strip()
    try:
        window["LOG"].update(new)
    except Exception:
        pass

def build_table_values_list(mod_sources_ordered, used_ranges, source_to_type=None):
    """
    Строит список значений для таблицы модов.
    source_to_type: dict source -> mod_type string
    """
    out = []
    for idx, src in enumerate(mod_sources_ordered, 1):
        rng = used_ranges.get(src, "")
        mod_type = source_to_type.get(src, "") if source_to_type else ""
        out.append([idx, src, mod_type, rng])
    return out

def safe_table_indices(value):
    if not isinstance(value, list):
        return []
    return [i for i in value if isinstance(i, int)]

def save_session(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_session(file_path):
    with open(file_path, encoding="utf-8") as f:
        return json.load(f)

def update_mods_table(window, display_sources, used_ranges, sort_key=None, reverse=False, 
                      source_to_folder=None, conflict_folders=None, source_to_type=None):
    """
    display_sources: list of source strings in the order to show
    used_ranges: dict source->range string
    sort_key: column index to sort by (0,1,2,3)
    conflict_folders: set of folder names (to color rows)
    source_to_folder: mapping source->folder for checking conflicts
    source_to_type: mapping source->mod_type string
    """
    rows = build_table_values_list(display_sources, used_ranges, source_to_type)
    if sort_key is not None:
        def key_func(row):
            val = row[sort_key]
            if sort_key == 0:
                try:
                    return int(val)
                except Exception:
                    return 0
            if sort_key == 3:  # колонка "Used priorities"
                if isinstance(val, str) and val:
                    m = re.match(r"\s*(-?\d+)", val)
                    if m:
                        try:
                            return int(m.group(1))
                        except Exception:
                            return val
                return ""
            return str(val).lower()
        rows.sort(key=key_func, reverse=reverse)

    row_colors = []
    if conflict_folders and source_to_folder:
        for i, r in enumerate(rows):
            src = r[1]
            mapped = source_to_folder.get(src)
            if mapped and mapped in conflict_folders:
                row_colors.append((i, "white", "red"))

    try:
        window["MODS_TABLE"].update(values=rows, row_colors=row_colors)
    except Exception:
        pass

# ==== Mode chooser UI ====

def choose_mode():
    layout = [
        [sg.Text("Select working mode:")],
        [sg.Button("MO2", size=(10,1)), sg.Button("Vortex", size=(10,1))],
        [sg.Button("Exit")]
    ]
    win = sg.Window("PriOARity — Mode selection", layout, modal=True, element_justification="center")
    mode = None
    while True:
        event, _ = win.read()
        if event in (sg.WIN_CLOSED, "Exit"):
            break
        if event in ("MO2", "Vortex"):
            mode = event
            break
    win.close()
    return mode

# ==== Per-mode UIs (simple separate windows) ====

def build_common_ui(title="PriOARity", input_label="Profile / Deployment", folder_mode=False):
    sg.change_look_and_feel("DarkGrey9")
    INPUT_WIDTH = 80
    LIST_HEIGHT = 20
    LOG_HEIGHT = 16

    browse_btn = sg.FolderBrowse("Browse") if folder_mode else sg.FileBrowse("Browse")
    layout = [
        [sg.Text(title, font=("Default", 14, "bold"))],
        [sg.Frame("Session", [
            [sg.Button("Save session", size=(14,1)), sg.Button("Load session", size=(14,1))]
            ], pad=(8,8))],
        [sg.Frame("Settings", [
            [sg.Text(f"{input_label}:", size=(46,1)),
             sg.InputText(key="PROFILE_OR_DEPLOY", size=(INPUT_WIDTH,1)), browse_btn],
            [sg.Text("Mods / Staging folder (optional):", size=(46,1)),
             sg.InputText(key="MODS_DIR", default_text="If empty, defaults will be used", size=(INPUT_WIDTH,1)), sg.FolderBrowse("Browse")],
            [sg.Text("Output path:", size=(46,1)),
             sg.InputText(key="OUTPUT_DIR", size=(INPUT_WIDTH,1)), sg.FolderBrowse("Browse")],
            [sg.Text("Start priority:", size=(46,1)), sg.InputText("1", key="START_PRIORITY", size=(10,1))],
            [sg.Button("Load mods", size=(12,1)), sg.Button("Check", size=(10,1)), sg.Button("Run", button_color=("white","green"), size=(10,1)),
             sg.Checkbox("Manual order", key="MANUAL_ORDER", default=False)],
            [sg.Checkbox("Include DAR Legacy mods", key="INCLUDE_DAR", default=True, 
                        tooltip="Scan for DAR Legacy animation structures and include them in priority assignment")],
        ], pad=(8,8), expand_x=True)],

        [sg.Frame("Detected animation mods (table):", [
            [sg.Table(values=[],
                      headings=["№", "Mod Name / Source", "Type", "Used priorities"],
                      key="MODS_TABLE",
                      auto_size_columns=False,
                      col_widths=[6, 70, 20, 25],
                      enable_events=True,
                      expand_x=True, expand_y=True,
                      justification="left",
                      select_mode=sg.TABLE_SELECT_MODE_EXTENDED,
                      num_rows=LIST_HEIGHT,
                      row_colors=[]
                      )]
        ], pad=(8,8), expand_x=True, expand_y=True)],
        [sg.Frame("Execution log", [
            [sg.Multiline(size=(INPUT_WIDTH, LOG_HEIGHT), key="LOG", autoscroll=True, disabled=True, expand_x=True)]
        ], pad=(8,8), expand_x=True)]
    ]
    return sg.Window(title, layout, resizable=True)

# ==== Run MO2 mode ====

def run_mo2_mode():
    window = build_common_ui(title="PriOARity — MO2 mode", input_label="MO2 Profile folder", folder_mode=True)

    mods_dir = None
    mod_sources_ordered = []
    display_sources = []
    source_to_folder = {}
    source_to_type = {}  # source -> mod_type string
    used_ranges = {}
    sort_column = None
    sort_reverse = False

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Exit"):
            window.close()
            return "exit"
        if event == "Back":
            window.close()
            return "back"

        mode = "MO2"

        if event == "Load mods":
            window["LOG"].update("")
            used_ranges = {}
            sort_column = None
            sort_reverse = False
            source_to_folder = {}
            source_to_type = {}
            mod_sources_ordered = []
            display_sources = []

            profile_path = values.get("PROFILE_OR_DEPLOY") or ""
            if not profile_path or not os.path.isdir(profile_path):
                sg.popup_error("Please select a valid MO2 profile folder.")
                continue

            modlist_file = os.path.join(profile_path, "modlist.txt")
            if not os.path.exists(modlist_file):
                sg.popup_error("modlist.txt not found in profile folder.")
                continue

            mods_dir_input = (values.get("MODS_DIR") or "").strip()
            if mods_dir_input and os.path.isdir(mods_dir_input):
                mods_dir = mods_dir_input
            else:
                mods_dir = os.path.abspath(os.path.join(profile_path, "..", "..", "mods"))
            if not os.path.isdir(mods_dir):
                sg.popup_error(f"Mods folder not found: {mods_dir}")
                continue

            # read active mods
            with open(modlist_file, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            active_mods = [line[1:].strip() for line in lines if line.startswith("+")]
            active_mods.reverse()  # first in list = loaded last

            # filter animation mods (OAR + DAR Legacy)
            include_dar = values.get("INCLUDE_DAR", True)
            for m in active_mods:
                mod_path = os.path.join(mods_dir, m)
                mod_type = detect_mod_type(mod_path)
                if mod_type:
                    mod_sources_ordered.append(m)
                    source_to_folder[m] = m
                    source_to_type[m] = mod_type

            display_sources = list(mod_sources_ordered)
            table_values = build_table_values_list(display_sources, used_ranges, source_to_type)
            try:
                window["MODS_TABLE"].update(values=table_values, row_colors=[])
            except Exception:
                pass
            
            oar_count = sum(1 for t in source_to_type.values() if t in (ModType.OAR, ModType.MIXED))
            dar_count = sum(1 for t in source_to_type.values() if t in (ModType.DAR_LEGACY_CUSTOM, ModType.DAR_LEGACY_ACTOR, ModType.MIXED))
            append_log(window, f"{len(mod_sources_ordered)} animation mods loaded from MO2 profile.")
            append_log(window, f"  - OAR mods: {oar_count}")
            if include_dar:
                append_log(window, f"  - DAR Legacy mods: {dar_count}")
            append_log(window, f"Mods folder: {mods_dir}")

        if event == "Check":
            if not mod_sources_ordered:
                sg.popup_error("No mods loaded. Please load mods first.")
                continue
            
            include_dar = values.get("INCLUDE_DAR", True)
            append_log(window, f"Checking for duplicate priorities among {len(mod_sources_ordered)} mods...")
            if include_dar:
                append_log(window, "  (including DAR Legacy mods)")
            
            # Always scan in canonical original order
            all_folders = [source_to_folder[s] for s in mod_sources_ordered if s in source_to_folder]
            if not all_folders:
                append_log(window, "No mapped folders found, cannot scan.")
                continue
            try:
                duplicate_conflicts = find_priority_conflicts(mods_dir, all_folders, include_dar_legacy=include_dar)
            except Exception as e:
                append_log(window, f"Error scanning priorities: {e}")
                continue
            
            # compute used ranges per source
            used_ranges = {}
            for src in mod_sources_ordered:
                folder = source_to_folder.get(src)
                if not folder:
                    continue
                mod_path = os.path.join(mods_dir, folder)
                
                # OAR priorities
                try:
                    jsons = collect_jsons(mod_path)
                    pri_values = [int(j[4]) for j in jsons if "priority" in j[3]]
                    if pri_values:
                        used_ranges[src] = f"{min(pri_values)} - {max(pri_values)}"
                except RuntimeError:
                    pass
                
                # DAR Legacy priorities
                if include_dar:
                    dar_entries = collect_dar_legacy_entries(mod_path)
                    dar_pri_values = [int(e[4]) for e in dar_entries]
                    if dar_pri_values:
                        dar_range = f"{min(dar_pri_values)} - {max(dar_pri_values)}"
                        if src in used_ranges:
                            used_ranges[src] = f"{used_ranges[src]} + {dar_range} (DAR)"
                        else:
                            used_ranges[src] = f"{dar_range} (DAR)"
            
            # conflict folders
            conflict_folders = set()
            for pri, mods in duplicate_conflicts:
                conflict_folders.update(mods)

            # update table
            update_mods_table(window, display_sources, used_ranges, sort_key=sort_column, 
                            reverse=sort_reverse, source_to_folder=source_to_folder, 
                            conflict_folders=conflict_folders, source_to_type=source_to_type)

            # log
            log_lines = []
            if duplicate_conflicts:
                log_lines.append("❌ Duplicate priority conflicts detected:")
                for pri, mods in sorted(duplicate_conflicts, key=lambda x: x[0]):
                    log_lines.append(f" - Priority {pri}: mods: {', '.join(mods)}")
            else:
                log_lines.append("✅ No duplicate priorities detected.")
            window["LOG"].update("\n".join(log_lines))
            append_log(window, "Check finished.")
        
        if event == "Save session":
            path = sg.popup_get_file(
                "Save session",
                save_as=True,
                file_types=(("PriOARity session", "*.prioarity.json"),),
                default_extension=".prioarity.json"
            )
            if not path:
                continue
            
            selected_indices = safe_table_indices(values.get("MODS_TABLE"))
            session = {
                "version": 1,
                "mode": "MO2",  # или "Vortex"
                "paths": {
                    "profile_or_deploy": values.get("PROFILE_OR_DEPLOY"),
                    "mods_dir": values.get("MODS_DIR"),
                    "output_dir": values.get("OUTPUT_DIR"),
                },
                "start_priority": values.get("START_PRIORITY"),
                "manual_order": values.get("MANUAL_ORDER", False),
                "include_dar": values.get("INCLUDE_DAR", True),
                "mod_sources_ordered": mod_sources_ordered,
                "display_sources": display_sources,
                "source_to_type": source_to_type,  # сохраняем типы модов
                "selected_sources": [
                    display_sources[i]
                    for i in selected_indices
                    if 0 <= i < len(display_sources)
                ]
            }

            save_session(path, session)
            append_log(window, f"Session saved: {path}")
            
        if event == "Load session":
            duplicate_conflicts = []
            path = sg.popup_get_file(
                "Load session",
                file_types=(("PriOARity session", "*.prioarity.json"),)
            )
            if not path:
                continue

            try:
                session = load_session(path)
            except Exception as e:
                sg.popup_error(f"Failed to load session:\n{e}")
                continue

            # basic validation
            if session.get("mode") != "MO2":
                sg.popup_error("This session was saved for another mode.")
                continue

            # restore paths
            window["PROFILE_OR_DEPLOY"].update(session["paths"].get("profile_or_deploy", ""))
            window["MODS_DIR"].update(session["paths"].get("mods_dir", ""))
            window["OUTPUT_DIR"].update(session["paths"].get("output_dir", ""))
            window["START_PRIORITY"].update(session.get("start_priority", "1"))
            window["MANUAL_ORDER"].update(session.get("manual_order", False))
            window["INCLUDE_DAR"].update(session.get("include_dar", True))

            # restore order
            mod_sources_ordered = session.get("mod_sources_ordered", [])
            display_sources = session.get("display_sources", list(mod_sources_ordered))
            source_to_type = session.get("source_to_type", {})

            used_ranges = {}
            table_values = build_table_values_list(display_sources, used_ranges, source_to_type)
            window["MODS_TABLE"].update(values=table_values, row_colors=[])

            append_log(window, f"Session loaded: {path}")

            # compute used ranges
            used_ranges = {}
            for src in mod_sources_ordered:
                folder = source_to_folder.get(src)
                if not folder:
                    continue
                mod_path = os.path.join(mods_dir, folder)
                try:
                    jsons = collect_jsons(mod_path)
                except RuntimeError:
                    continue
                if not jsons:
                    continue
                pri_values = []
                for j in jsons:
                    try:
                        pri_values.append(int(j[4]))
                    except Exception:
                        pass
                if pri_values:
                    used_ranges[src] = f"{min(pri_values)} - {max(pri_values)}"

            # conflict folders
            conflict_folders = set()
            for pri, mods in duplicate_conflicts:
                conflict_folders.update(mods)

            # update table, keep current display ordering if sorted
            update_mods_table(window, display_sources, used_ranges, sort_key=sort_column, reverse=sort_reverse, source_to_folder=source_to_folder, conflict_folders=conflict_folders)

            # log
            log_lines = []
            if duplicate_conflicts:
                log_lines.append("❌ Duplicate priority conflicts detected:")
                for pri, mods in sorted(duplicate_conflicts, key=lambda x: x[0]):
                    log_lines.append(f" - Priority {pri}: mods: {', '.join(mods)}")
            else:
                log_lines.append("No duplicate priorities detected.")
            window["LOG"].update("\n".join(log_lines))
            append_log(window, "Check finished.")

        if event == "Run":
            selected_rows = safe_table_indices(values.get("MODS_TABLE"))
            if not selected_rows:
                sg.popup_error("No mods selected in the table.")
                continue
            output_dir = values.get("OUTPUT_DIR") or ""
            if not output_dir:
                sg.popup_error("Please select a valid output folder.")
                continue
            try:
                start_priority = int(values.get("START_PRIORITY", 1))
                if start_priority < 1:
                    raise ValueError
            except Exception:
                sg.popup_error("Start priority must be integer (>=1).")
                continue

            selected_mods = []
            for i in selected_rows:
                if i < len(display_sources):
                    selected_mods.append(display_sources[i])
            if values.get("MANUAL_ORDER"):
                new_order = manual_order_window(selected_mods)
                if new_order is None:  # cancel
                    continue
                selected_mods = new_order
            
            include_dar = values.get("INCLUDE_DAR", True)
            
            out_root = os.path.join(output_dir, "PriOARity_Output")
            os.makedirs(out_root, exist_ok=True)

            log_lines = []
            priority_counter = start_priority
            for mod in selected_mods:
                mod_folder_path = os.path.join(mods_dir, mod)
                append_log(window, f"Processing mod '{mod}'")
                try:
                    priority_counter = copy_jsons_from_mod(mod_folder_path, out_root, mod, priority_counter, log_lines, include_dar_legacy=include_dar)
                except RuntimeError as e:
                    append_log(window, f"Error processing '{mod}': {e}")

            window["LOG"].update("\n".join(log_lines))
            logfile_name = os.path.join(out_root, f"mo2_prio_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
            try:
                with open(logfile_name, "w", encoding=LOG_ENCODING) as lf:
                    lf.write("\n".join(log_lines))
                append_log(window, f"Done! Log saved to: {logfile_name}")
            except Exception as e:
                append_log(window, f"Failed to save log: {e}")

        # Table header click for sorting
        if isinstance(event, tuple) and event[0] == "MODS_TABLE":
            # ожидаем формат ('MODS_TABLE', '+CLICKED+', (row, col))
            if len(event) >= 3 and isinstance(event[2], tuple):
                row, col = event[2]
                if row == -1 and col in (0, 1, 2, 3):  # клик по заголовку (0=№, 1=Name, 2=Type, 3=Priorities)
                    if sort_column == col:
                        sort_reverse = not sort_reverse
                    else:
                        sort_reverse = False
                    sort_column = col

                    table_rows = build_table_values_list(display_sources, used_ranges, source_to_type)

                    def key_func(row):
                        val = row[col]
                        if col == 0:
                            return int(val)
                        if col == 3 and isinstance(val, str):  # колонка "Used priorities"
                            m = re.match(r"\s*(-?\d+)", val)
                            return int(m.group(1)) if m else 0
                        return str(val).lower()

                    table_rows.sort(key=key_func, reverse=sort_reverse)
                    display_sources = [r[1] for r in table_rows]

                    # пересветка конфликтов
                    conflict_folders = set()
                    include_dar = values.get("INCLUDE_DAR", True)
                    try:
                        all_folders = [
                            source_to_folder.get(s)
                            for s in mod_sources_ordered
                            if source_to_folder.get(s)
                        ]
                        for pri, mods in find_priority_conflicts(mods_dir, all_folders, include_dar_legacy=include_dar):
                            conflict_folders.update(mods)
                    except Exception:
                        pass

                    update_mods_table(
                        window,
                        display_sources,
                        used_ranges,
                        sort_key=col,
                        reverse=sort_reverse,
                        source_to_folder=source_to_folder,
                        conflict_folders=conflict_folders,
                        source_to_type=source_to_type
                    )


    # unreachable

# ==== Run Vortex mode ====

def run_vortex_mode():
    window = build_common_ui(title="PriOARity — Vortex mode", input_label="vortex.deployment.msgpack", folder_mode=False)

    mods_dir = None
    mod_sources_ordered = []
    display_sources = []
    source_to_folder = {}
    source_to_type = {}  # source -> mod_type string
    used_ranges = {}
    sort_column = None
    sort_reverse = False

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Exit"):
            window.close()
            return "exit"
        if event == "Back":
            window.close()
            return "back"

        if event == "Load mods":
            window["LOG"].update("")
            used_ranges = {}
            sort_column = None
            sort_reverse = False
            source_to_folder = {}
            source_to_type = {}
            mod_sources_ordered = []
            display_sources = []

            deployment_file = values.get("PROFILE_OR_DEPLOY") or ""
            if not deployment_file or not os.path.exists(deployment_file):
                sg.popup_error("Please select a valid vortex.deployment.msgpack file.")
                continue
            try:
                deployment_data = load_vortex_deployment(deployment_file)
            except Exception as e:
                sg.popup_error(f"Failed to load deployment: {e}")
                continue

            user_staging = (values.get("MODS_DIR") or "").strip()
            staging_candidate = None
            if user_staging and os.path.isdir(user_staging):
                staging_candidate = user_staging
            else:
                stpath = deployment_data.get("stagingPath")
                if stpath and os.path.isdir(stpath):
                    p1 = os.path.join(stpath, "mods")
                    staging_candidate = p1 if os.path.isdir(p1) else stpath

            if not staging_candidate:
                append_log(window, "Warning: could not determine staging folder automatically.")
                append_log(window, "Please specify staging mods folder manually (the folder that contains mod subfolders).")
                mods_dir = None
            else:
                mods_dir = staging_candidate
                append_log(window, f"Using staging folder: {mods_dir}")

            entries = deployment_data.get("entries", []) or []
            append_log(window, f"Total deployment entries found: {len(entries)}")

            mod_sources_ordered = extract_ordered_sources_from_entries(entries)
            append_log(window, f"Detected {len(mod_sources_ordered)} distinct OAR/DAR sources in deployment (in order).")

            # map sources -> folders if staging known (progress meter)
            source_to_folder.clear()
            source_to_type.clear()
            if mods_dir:
                try:
                    _ = [d for d in os.listdir(mods_dir) if os.path.isdir(os.path.join(mods_dir, d))]
                except Exception as e:
                    append_log(window, f"Error listing staging folder contents: {e}")
                total = len(mod_sources_ordered)
                include_dar = values.get("INCLUDE_DAR", True)
                for i, src in enumerate(mod_sources_ordered, 1):
                    sg.OneLineProgressMeter("Mapping sources", i, total, "MAPSRC", f"Scanning {i}/{total} sources...")
                    folder = find_mod_folder_by_source(mods_dir, src)
                    if folder:
                        source_to_folder[src] = folder
                        # detect mod type
                        mod_path = os.path.join(mods_dir, folder)
                        mod_type = detect_mod_type(mod_path)
                        if mod_type and (include_dar or mod_type in (ModType.OAR, ModType.MIXED)):
                            source_to_type[src] = mod_type
                            append_log(window, f"Mapped source -> folder: '{src}' → '{folder}' [{mod_type}]")
                        elif mod_type:
                            append_log(window, f"Mapped source -> folder: '{src}' → '{folder}' [{mod_type}]")
                        else:
                            append_log(window, f"Mapped source -> folder: '{src}' → '{folder}' (no animations)")
                    else:
                        append_log(window, f"Could not map source to folder (staging scan): '{src}'")

            display_sources = list(mod_sources_ordered)
            table_values = build_table_values_list(display_sources, used_ranges, source_to_type)
            try:
                window["MODS_TABLE"].update(values=table_values, row_colors=[])
            except Exception:
                pass
            
            oar_count = sum(1 for t in source_to_type.values() if t in (ModType.OAR, ModType.MIXED))
            dar_count = sum(1 for t in source_to_type.values() if t in (ModType.DAR_LEGACY_CUSTOM, ModType.DAR_LEGACY_ACTOR, ModType.MIXED))
            append_log(window, f"Load complete. {oar_count} OAR mods, {dar_count} DAR Legacy mods detected.")
            append_log(window, "Select rows and click Run, or click Check to scan duplicates.")

        if event == "Check":
            if not mod_sources_ordered:
                sg.popup_error("No mods loaded. Please load mods first.")
                continue
            
            include_dar = values.get("INCLUDE_DAR", True)
            append_log(window, f"Running duplicate-priority check on {len(mod_sources_ordered)} detected mods...")
            if include_dar:
                append_log(window, "  (including DAR Legacy mods)")

            all_folders_ordered = []
            unmapped = []
            for src in mod_sources_ordered:
                folder = source_to_folder.get(src)
                if folder:
                    all_folders_ordered.append(folder)
                else:
                    unmapped.append(src)
            if unmapped:
                append_log(window, f"Warning: some sources were not mapped to folders and will be skipped: {unmapped}")
            if not all_folders_ordered:
                append_log(window, "No mapped folders available for scanning.")
                continue

            try:
                duplicate_conflicts = find_priority_conflicts(mods_dir, all_folders_ordered, include_dar_legacy=include_dar)
            except Exception as e:
                append_log(window, f"Error while scanning priorities: {e}")
                continue

            # compute used ranges per source (scan per canonical source order)
            used_ranges = {}
            for src in mod_sources_ordered:
                folder = source_to_folder.get(src)
                if not folder:
                    continue
                mod_path = os.path.join(mods_dir, folder)
                
                # OAR priorities
                try:
                    jsons = collect_jsons(mod_path)
                    pri_values = [int(j[4]) for j in jsons if "priority" in j[3]]
                    if pri_values:
                        used_ranges[src] = f"{min(pri_values)} - {max(pri_values)}"
                except RuntimeError:
                    pass
                
                # DAR Legacy priorities
                if include_dar:
                    dar_entries = collect_dar_legacy_entries(mod_path)
                    dar_pri_values = [int(e[4]) for e in dar_entries]
                    if dar_pri_values:
                        dar_range = f"{min(dar_pri_values)} - {max(dar_pri_values)}"
                        if src in used_ranges:
                            used_ranges[src] = f"{used_ranges[src]} + {dar_range} (DAR)"
                        else:
                            used_ranges[src] = f"{dar_range} (DAR)"

            # conflict folders
            conflict_folders = set()
            for pri, mods in duplicate_conflicts:
                conflict_folders.update(mods)

            # update table (keep current display ordering)
            update_mods_table(window, display_sources, used_ranges, sort_key=sort_column, 
                            reverse=sort_reverse, source_to_folder=source_to_folder, 
                            conflict_folders=conflict_folders, source_to_type=source_to_type)

            # log
            log_lines = []
            if duplicate_conflicts:
                log_lines.append("❌ Duplicate priority conflicts detected:")
                for pri, mods in sorted(duplicate_conflicts, key=lambda x: x[0]):
                    mapped_sources = [k for k, v in source_to_folder.items() if v in mods]
                    display_mods = ", ".join(mods)
                    display_srcs = ", ".join(mapped_sources) if mapped_sources else "(no source mapping)"
                    log_lines.append(f" - Priority {pri}: folders: {display_mods}; sources: {display_srcs}")
            else:
                log_lines.append("✅ No duplicate priorities detected.")
            window["LOG"].update("\n".join(log_lines))
            append_log(window, "Duplicate check finished.")

        if event == "Run":
            selected_rows = safe_table_indices(values.get("MODS_TABLE"))
            if not selected_rows:
                sg.popup_error("No mods (rows) selected in the table.")
                continue
            output_dir = values.get("OUTPUT_DIR") or ""
            if not output_dir:
                sg.popup_error("Please select a valid output directory.")
                continue
            try:
                start_priority = int(values.get("START_PRIORITY", 1))
                if start_priority < 1:
                    raise ValueError
            except Exception:
                sg.popup_error("Start priority must be integer (>=1).")
                continue

            # map displayed indices -> sources
            selected_sources = []
            for i in selected_rows:
                if i < len(display_sources):
                    selected_sources.append(display_sources[i])

            if values.get("MANUAL_ORDER"):
                new_order = manual_order_window(selected_sources)
                if new_order is None:  # cancel
                    continue
                selected_sources = new_order

            # map sources -> folders
            selected_mapped_folders = []
            unmapped = []
            for src in selected_sources:
                folder = source_to_folder.get(src)
                if folder:
                    selected_mapped_folders.append((src, folder))
                else:
                    unmapped.append(src)
            if unmapped:
                append_log(window, f"Warning: some selected sources were not mapped and will be skipped: {unmapped}")
            if not selected_mapped_folders:
                append_log(window, "No mapped folders to process. Aborting Run.")
                continue

            include_dar = values.get("INCLUDE_DAR", True)
            
            out_root = os.path.join(output_dir, "PriOARity_Output")
            os.makedirs(out_root, exist_ok=True)

            log_lines = []
            priority_counter = start_priority
            for src, folder in selected_mapped_folders:
                mod_folder_path = os.path.join(mods_dir, folder)
                if not os.path.exists(mod_folder_path):
                    append_log(window, f"Skipping missing folder '{mod_folder_path}' for source '{src}'")
                    continue
                append_log(window, f"Processing source '{src}' -> folder '{folder}'")
                try:
                    priority_counter = copy_jsons_from_mod(mod_folder_path, out_root, src, priority_counter, log_lines, include_dar_legacy=include_dar)
                except RuntimeError as e:
                    append_log(window, f"Error processing '{src}': {e}")

            log_text = "\n".join(log_lines) if log_lines else "(no json files found / nothing processed)"
            window["LOG"].update(log_text)
            logfile_name = os.path.join(out_root, f"vortex_prio_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
            try:
                with open(logfile_name, "w", encoding=LOG_ENCODING) as lf:
                    lf.write(log_text)
                append_log(window, f"Done! Log saved to: {logfile_name}")
            except Exception as e:
                append_log(window, f"Failed to save log file: {e}")

        # Table header click for sorting
        if isinstance(event, tuple) and event[0] == "MODS_TABLE":
            try:
                pos = event[2]
            except Exception:
                pos = None
            if pos and isinstance(pos, (list, tuple)) and len(pos) >= 2 and pos[0] == -1:
                col = pos[1]
                if col not in (0, 1, 2, 3):  # 0=№, 1=Name, 2=Type, 3=Priorities
                    continue
                if sort_column == col:
                    sort_reverse = not sort_reverse
                else:
                    sort_reverse = False
                sort_column = col
                # build rows and sort
                table_rows = build_table_values_list(display_sources, used_ranges, source_to_type)
                def key_func(row):
                    val = row[col]
                    if col == 0:
                        try:
                            return int(val)
                        except Exception:
                            return 0
                    if col == 3:  # колонка "Used priorities"
                        if isinstance(val, str) and val:
                            m = re.match(r"\s*(-?\d+)", val)
                            if m:
                                try:
                                    return int(m.group(1))
                                except Exception:
                                    return val
                        return ""
                    return str(val).lower()
                table_rows.sort(key=key_func, reverse=sort_reverse)
                display_sources = [r[1] for r in table_rows]
                # recompute conflict_folders (quick scan)
                conflict_folders = set()
                include_dar = values.get("INCLUDE_DAR", True)
                try:
                    all_folders = [source_to_folder.get(s) for s in mod_sources_ordered if source_to_folder.get(s)]
                    duplicate_conflicts = find_priority_conflicts(mods_dir, [f for f in all_folders if f], include_dar_legacy=include_dar)
                    for pri, mods in duplicate_conflicts:
                        conflict_folders.update(mods)
                except Exception:
                    conflict_folders = set()
                update_mods_table(window, display_sources, used_ranges, sort_key=col, reverse=sort_reverse, source_to_folder=source_to_folder, conflict_folders=conflict_folders, source_to_type=source_to_type)

    # unreachable

# ==== Main launcher ====

def main():
    sg.change_look_and_feel("DarkGrey9")
    while True:
        mode = choose_mode()
        if not mode:
            break
        if mode == "MO2":
            action = run_mo2_mode()
            if action == "exit":
                break
            # "back" returns to chooser
        elif mode == "Vortex":
            action = run_vortex_mode()
            if action == "exit":
                break
    print("Exiting PriOARity.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fatal error:", e)
