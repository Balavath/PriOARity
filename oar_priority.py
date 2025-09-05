import os
import json
import re
import FreeSimpleGUI as sg
from datetime import datetime

# ==== Config / constants ====
OAR_KEYWORD = "OpenAnimationReplacer"
LOG_ENCODING = "utf-8"

# ==== Helpers ====

def is_oar_mod(mod_path):
    """Проверяет, содержит ли мод OAR-анимации."""
    for root, dirs, _ in os.walk(mod_path):
        if OAR_KEYWORD in root or OAR_KEYWORD in " ".join(dirs):
            return True
    return False


def collect_jsons(mod_path):
    """Собирает JSON-файлы внутри OAR-папок."""
    entries = []
    for root, _, files in os.walk(mod_path):
        if OAR_KEYWORD not in root:
            continue
        rel_path = os.path.relpath(root, mod_path)
        for file in files:
            if file.lower().endswith(".json"):
                src_file = os.path.join(root, file)
                try:
                    with open(src_file, encoding=LOG_ENCODING) as f:
                        data = json.load(f)
                    old_pri = data.get("priority", 0)
                except Exception as e:
                    raise RuntimeError(f"Read error {src_file}: {e}")
                entries.append((src_file, rel_path, file, data, old_pri))
    entries.sort(key=lambda x: x[4])
    return entries


def copy_jsons_from_mod(mod_folder_path, out_dir, mod_display_name, priority_counter, log_lines):
    """Копирование и перерасчет priority"""
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
    return priority_counter


def find_priority_conflicts(mods_dir, selected_mods_ordered):
    """Проверка на дубли приоритетов между выбранными модами."""
    all_entries = []
    for load_index, mod in enumerate(selected_mods_ordered):
        mod_path = os.path.join(mods_dir, mod)
        if not os.path.exists(mod_path):
            continue
        try:
            jsons = collect_jsons(mod_path)
        except RuntimeError:
            continue
        for _, _, file_name, data, priority in jsons:
            try:
                pri_val = int(priority)
            except Exception:
                pri_val = priority
            all_entries.append((mod, file_name, pri_val, load_index))

    pri_map = {}
    for mod, file_name, pri, _ in all_entries:
        pri_map.setdefault(pri, set()).add(mod)

    duplicate_conflicts = [(pri, sorted(list(folders))) for pri, folders in pri_map.items() if len(folders) > 1]
    return duplicate_conflicts


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


def build_mo2_ui():
    sg.change_look_and_feel("DarkGrey9")
    INPUT_WIDTH = 70
    LIST_HEIGHT = 20
    LOG_HEIGHT = 15

    layout = [
        [sg.Frame("Settings", [
            [sg.Text("MO2 Profile path:", size=(20, 1)),
             sg.InputText(key="PROFILE", size=(INPUT_WIDTH, 1)), sg.FolderBrowse("Browse")],
            [sg.Text("MO2 Mods path (optional):", size=(20, 1)),
             sg.InputText(key="MODS_DIR", default_text="If empty, will use profile-relative default", size=(INPUT_WIDTH, 1)), sg.FolderBrowse("Browse")],
            [sg.Text("Output path:", size=(20, 1)),
             sg.InputText(key="OUTPUT_DIR", size=(INPUT_WIDTH, 1)), sg.FolderBrowse("Browse")],
            [sg.Text("Start priority:", size=(20, 1)),
             sg.InputText("1", key="START_PRIORITY", size=(10, 1))],
            [sg.Button("Load mods", size=(14, 1)), sg.Button("Check", size=(10, 1)),
             sg.Button("Run", button_color=("white", "green"), size=(10, 1)), sg.Button("Exit", size=(10, 1))]
        ], pad=(8, 8), element_justification='left', expand_x=True)],
        [sg.Frame("Detected OAR mods (profile order):", [
            [sg.Table(values=[], headings=["Mod Name"], key="MODS_TABLE",
                      auto_size_columns=True, col_widths=[90], enable_events=True,
                      expand_x=True, expand_y=True, justification="left",
                      select_mode=sg.TABLE_SELECT_MODE_EXTENDED,
                      num_rows=LIST_HEIGHT,
                      row_colors=[])]
        ], pad=(8, 8), element_justification='left', expand_x=True)],
        [sg.Frame("Execution log", [
            [sg.Multiline(size=(INPUT_WIDTH, LOG_HEIGHT), key="LOG", autoscroll=True, disabled=True, expand_x=True)]
        ], pad=(8, 8), element_justification='left', expand_x=True)]
    ]
    return sg.Window("PriOARity — MO2 mode", layout, resizable=True)


# ==== Main ====

def main():
    window = build_mo2_ui()
    mods_dir = None
    mod_sources_ordered = []
    source_to_folder = {}

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Exit"):
            break

        if event == "Load mods":
            window["LOG"].update("")
            profile_path = values.get("PROFILE") or ""
            if not profile_path or not os.path.exists(profile_path):
                sg.popup_error("Please select a valid MO2 profile folder.")
                continue

            modlist_file = os.path.join(profile_path, "modlist.txt")
            if not os.path.exists(modlist_file):
                sg.popup_error("modlist.txt not found in the profile folder.")
                continue

            mods_dir_input = (values.get("MODS_DIR") or "").strip()
            if mods_dir_input and os.path.isdir(mods_dir_input):
                mods_dir = mods_dir_input
            else:
                mods_dir = os.path.abspath(os.path.join(profile_path, "..", "..", "mods"))

            if not os.path.exists(mods_dir):
                sg.popup_error(f"Mods folder not found: {mods_dir}")
                continue

            # читаем активные моды
            with open(modlist_file, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            active_mods = [line[1:].strip() for line in lines if line.startswith("+")]
            active_mods.reverse()  # чтобы первый загружался последним

            # фильтр по OAR
            mod_sources_ordered = [m for m in active_mods if is_oar_mod(os.path.join(mods_dir, m))]
            source_to_folder = {m: m for m in mod_sources_ordered}

            # таблица
            table_values = [[m] for m in mod_sources_ordered]
            window["MODS_TABLE"].update(values=table_values, row_colors=[])

            append_log(window, f"{len(mod_sources_ordered)} OAR-mods loaded from profile.")
            append_log(window, f"Mods folder: {mods_dir}")

        if event == "Check":
            if not mod_sources_ordered:
                sg.popup_error("No mods loaded. Please load mods first.")
                continue

            append_log(window, f"Checking for duplicate priorities among {len(mod_sources_ordered)} mods...")
            duplicate_conflicts = find_priority_conflicts(mods_dir, mod_sources_ordered)

            # highlight duplicates in table
            row_colors = []
            conflict_mods = set()
            for pri, mods in duplicate_conflicts:
                conflict_mods.update(mods)
            for i, m in enumerate(mod_sources_ordered):
                if m in conflict_mods:
                    row_colors.append((i, "white", "red"))
            window["MODS_TABLE"].update(row_colors=row_colors)

            log_lines = []
            if duplicate_conflicts:
                log_lines.append("❌ Duplicate priority conflicts detected:")
                for pri, mods in sorted(duplicate_conflicts, key=lambda x: x[0]):
                    log_lines.append(f" - Priority {pri}: mods: {', '.join(mods)}")
            else:
                log_lines.append("No duplicate priorities detected.")
            window["LOG"].update("\n".join(log_lines))

        if event == "Run":
            selected_rows = values.get("MODS_TABLE") or []
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
                sg.popup_error("Start priority must be integer >= 1.")
                continue

            selected_mods = [mod_sources_ordered[i] for i in selected_rows if i < len(mod_sources_ordered)]
            out_root = os.path.join(output_dir, "PriOARity_Output")
            os.makedirs(out_root, exist_ok=True)

            log_lines = []
            priority_counter = start_priority
            for mod in selected_mods:
                mod_folder_path = os.path.join(mods_dir, mod)
                append_log(window, f"Processing mod '{mod}'")
                try:
                    priority_counter = copy_jsons_from_mod(mod_folder_path, out_root, mod, priority_counter, log_lines)
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

    window.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fatal error:", e)
