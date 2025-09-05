# pri_oarity_vortex.py
import os
import json
import msgpack
import re
import FreeSimpleGUI as sg
from datetime import datetime

# ==== Config / constants ====
OAR_KEYWORD = "OpenAnimationReplacer"
DAR_KEYWORD = "DynamicAnimationReplacer"  # optional
LOG_ENCODING = "utf-8"

# ==== Helpers ====


def is_oar_root_path(path_str):
    if not path_str:
        return False
    return OAR_KEYWORD.lower() in path_str.replace("/", "\\").lower()


def collect_jsons(mod_path):
    """
    Walk mod_path and collect JSON files that reside under OpenAnimationReplacer paths.
    Returns list of tuples: (src_file, rel_path (from mod_path), filename, data_dict, old_priority)
    """
    entries = []
    for root, _, files in os.walk(mod_path):
        if OAR_KEYWORD.lower() not in root.replace("/", "\\").lower():
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
                    # raise to let caller decide
                    raise RuntimeError(f"Read error {src_file}: {e}")
                entries.append((src_file, rel_path, file, data, old_pri))
    entries.sort(key=lambda x: x[4])
    return entries


def copy_jsons_from_mod(mod_folder_path, out_dir, mod_display_name, priority_counter, log_lines):
    """
    Copy JSONs from a single mod folder and rewrite priority sequentially.
    Returns updated priority_counter.
    """
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
    """
    Collects priorities from selected folders and returns:
      - order_conflicts (unused here)
      - duplicate_conflicts: list of (priority, [folders])
    selected_mods_ordered: list of folder names (not display names)
    """
    all_entries = []  # (mod_folder, file_name, priority, load_index)

    for load_index, mod in enumerate(selected_mods_ordered):
        mod_path = os.path.join(mods_dir, mod)
        if not os.path.exists(mod_path):
            continue
        try:
            jsons = collect_jsons(mod_path)
        except RuntimeError:
            # skip mod if jsons unreadable
            continue
        for _, _, file_name, data, priority in jsons:
            # ensure priority is int-like for grouping
            try:
                pri_val = int(priority)
            except Exception:
                pri_val = priority
            all_entries.append((mod, file_name, pri_val, load_index))

    # build map priority -> set(folders)
    pri_map = {}
    for mod, file_name, pri, load_index in all_entries:
        pri_map.setdefault(pri, set()).add(mod)

    duplicate_conflicts = [(pri, sorted(list(folders))) for pri, folders in pri_map.items() if len(folders) > 1]

    # order_conflicts left for future - return empty
    return [], duplicate_conflicts


# ==== Vortex parsing helpers ====


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


def build_vortex_ui():
    sg.change_look_and_feel("DarkGrey9")
    INPUT_WIDTH = 70
    LIST_HEIGHT = 20
    LOG_HEIGHT = 15

    layout = [
        [sg.Frame("Settings", [
            [sg.Text("Vortex deployment file (.msgpack):", size=(34, 1)),
             sg.InputText(key="DEPLOYMENT_FILE", size=(INPUT_WIDTH, 1)), sg.FileBrowse("Browse")],
            [sg.Text("Staging mods folder (optional):", size=(34, 1)),
             sg.InputText(key="STAGING_DIR", default_text="If empty, default path will be used", size=(INPUT_WIDTH, 1)), sg.FolderBrowse("Browse")],
            [sg.Text("Output path:", size=(34, 1)),
             sg.InputText(key="OUTPUT_DIR", size=(INPUT_WIDTH, 1)), sg.FolderBrowse("Browse")],
            [sg.Text("Start priority:", size=(34, 1)),
             sg.InputText("1", key="START_PRIORITY", size=(10, 1))],
            [sg.Button("Load mods", size=(14, 1)), sg.Button("Check", size=(10, 1)),
             sg.Button("Run", button_color=("white", "green"), size=(10, 1)), sg.Button("Exit", size=(10, 1))]
        ], pad=(8, 8), element_justification='left', expand_x=True)],
        [sg.Frame("Detected OAR mods (deployment order):", [
            [sg.Table(values=[],
                      headings=["Source (mapped folder)"],
                      key="MODS_TABLE",
                      auto_size_columns=True,
                      col_widths=[90],
                      enable_events=True,
                      expand_x=True,
                      expand_y=True,
                      justification="left",
                      select_mode=sg.TABLE_SELECT_MODE_EXTENDED,
                      num_rows=LIST_HEIGHT,
                      row_colors=[]  # we will set dynamically
                      )]
        ], pad=(8, 8), element_justification='left', expand_x=True)],
        [sg.Frame("Execution log", [
            [sg.Multiline(size=(INPUT_WIDTH, LOG_HEIGHT), key="LOG", autoscroll=True, disabled=True, expand_x=True)]
        ], pad=(8, 8), element_justification='left', expand_x=True)]
    ]
    return sg.Window("PriOARity — Vortex mode", layout, resizable=True)


def highlight_duplicates_table(window, duplicate_conflicts, mod_sources_ordered, source_to_folder):
    """
    Build row_colors for table: mark rows (by index) that correspond to folders involved in duplicates.
    Red background for conflicting rows.
    """
    conflict_folders = set()
    for pri, mods in duplicate_conflicts:
        for m in mods:
            conflict_folders.add(m)

    row_colors = []
    for i, src in enumerate(mod_sources_ordered):
        mapped = source_to_folder.get(src)
        if mapped and mapped in conflict_folders:
            row_colors.append((i, "white", "red"))
    try:
        # update values and row_colors
        table_values = [[f"{src}{(' [' + source_to_folder[src] + ']' ) if src in source_to_folder else ''}"] for src in mod_sources_ordered]
        window["MODS_TABLE"].update(values=table_values, row_colors=row_colors)
    except Exception:
        pass


# ==== Main ====

def main():
    window = build_vortex_ui()
    deployment_data = None
    staging_root = None
    entries = []
    mod_sources_ordered = []
    source_to_folder = {}
    mod_folders_list = []

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Exit"):
            break

        if event == "Load mods":
            window["LOG"].update("")  # clear
            deployment_file = values["DEPLOYMENT_FILE"]
            if not deployment_file or not os.path.exists(deployment_file):
                sg.popup_error("Please select a valid vortex.deployment.msgpack file.")
                continue

            try:
                deployment_data = load_vortex_deployment(deployment_file)
            except Exception as e:
                sg.popup_error(f"Failed to load deployment: {e}")
                continue

            # determine staging mods dir
            user_staging = (values.get("STAGING_DIR") or "").strip()
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
                staging_root = None
            else:
                staging_root = staging_candidate
                append_log(window, f"Using staging folder: {staging_root}")

            entries = deployment_data.get("entries", []) or []
            append_log(window, f"Total deployment entries found: {len(entries)}")

            mod_sources_ordered = extract_ordered_sources_from_entries(entries)
            append_log(window, f"Detected {len(mod_sources_ordered)} distinct OAR/DAR sources in deployment (in order).")

            # map sources -> folders if staging_root known
            source_to_folder.clear()
            mod_folders_list = []
            if staging_root:
                try:
                    mod_folders_list = [d for d in os.listdir(staging_root) if os.path.isdir(os.path.join(staging_root, d))]
                except Exception as e:
                    append_log(window, f"Error listing staging folder contents: {e}")
                    mod_folders_list = []

                for src in mod_sources_ordered:
                    folder = find_mod_folder_by_source(staging_root, src)
                    if folder:
                        source_to_folder[src] = folder
                        append_log(window, f"Mapped source -> folder: '{src}'  →  '{folder}'")
                    else:
                        append_log(window, f"Could not map source to folder (staging scan): '{src}'")

            # update table
            table_values = [[f"{src}{(' [' + source_to_folder[src] + ']' ) if src in source_to_folder else ''}"] for src in mod_sources_ordered]
            try:
                window["MODS_TABLE"].update(values=table_values, row_colors=[])
            except Exception:
                pass
            append_log(window, "Load complete. You can select mods (rows) and click Run, or click Check to scan duplicates.")

        if event == "Check":
            if not mod_sources_ordered:
                sg.popup_error("No mods loaded yet. Please load mods first.")
                continue

            append_log(window, f"Running global duplicate-priority check on {len(mod_sources_ordered)} detected mods...")

            # build ordered list of folder names we can analyze
            all_folders_ordered = []
            unmapped = []
            for src in mod_sources_ordered:
                folder = source_to_folder.get(src)
                if folder:
                    all_folders_ordered.append(folder)
                else:
                    unmapped.append(src)

            if unmapped:
                append_log(window, f"Warning: some sources were not mapped to staging folders and will be skipped: {unmapped}")

            if not all_folders_ordered:
                append_log(window, "No mapped folders found, cannot run duplicate-priority check.")
                continue

            try:
                _, duplicate_conflicts = find_priority_conflicts(staging_root, all_folders_ordered)
            except Exception as e:
                append_log(window, f"Error while scanning priorities: {e}")
                continue

            log_lines = []
            if duplicate_conflicts:
                log_lines.append("❌ Duplicate priority conflicts detected:")
                for pri, mods in sorted(duplicate_conflicts, key=lambda x: x[0]):
                    unique_mods = sorted(set(mods))
                    mapped_sources = [k for k, v in source_to_folder.items() if v in unique_mods]
                    display_mods = ", ".join(unique_mods)
                    display_srcs = ", ".join(mapped_sources) if mapped_sources else "(no source mapping)"
                    log_lines.append(f" - Priority {pri}: folders: {display_mods}; sources: {display_srcs}")
            else:
                log_lines.append("No duplicate priorities detected.")

            window["LOG"].update("\n".join(log_lines))

            # highlight duplicates in the table
            highlight_duplicates_table(window, duplicate_conflicts, mod_sources_ordered, source_to_folder)
            append_log(window, "Duplicate-priority check finished.")

        if event == "Run":
            # selection from table: list of row indices
            selected_rows = values.get("MODS_TABLE") or []
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

            # map selected rows -> sources
            selected_sources = [mod_sources_ordered[i] for i in selected_rows if i < len(mod_sources_ordered)]

            # map to folders
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

            out_root = os.path.join(output_dir, "PriOARity_Vortex_Output")
            os.makedirs(out_root, exist_ok=True)

            log_lines = []
            priority_counter = start_priority
            for src, folder in selected_mapped_folders:
                mod_folder_path = os.path.join(staging_root, folder)
                if not os.path.exists(mod_folder_path):
                    append_log(window, f"Skipping missing folder '{mod_folder_path}' for source '{src}'")
                    continue
                append_log(window, f"Processing source '{src}' -> folder '{folder}'")
                try:
                    priority_counter = copy_jsons_from_mod(mod_folder_path, out_root, src, priority_counter, log_lines)
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

    window.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fatal error:", e)
