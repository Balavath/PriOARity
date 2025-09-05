# pri_oarity_vortex.py
import os
import json
import msgpack
import re
import FreeSimpleGUI as sg
from datetime import datetime

# ==== Config / constants ====
OAR_KEYWORD = "OpenAnimationReplacer"
DAR_KEYWORD = "DynamicAnimationReplacer"  # optional, if you want to include DAR origins
LOG_ENCODING = "utf-8"


# ==== Reused helpers from your MO2 script ====


def is_oar_root_path(path_str):
    """Check if path string contains OpenAnimationReplacer (case-insensitive)."""
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
        # ignore everything unless the root path contains OpenAnimationReplacer somewhere
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
                    # return read error as a log message later
                    raise RuntimeError(f"Read error {src_file}: {e}")
                entries.append((src_file, rel_path, file, data, old_pri))
    entries.sort(key=lambda x: x[4])  # sort by old priority
    return entries


def copy_jsons_from_mod(mod_folder_path, out_dir, mod_display_name, start_priority, log_lines, priority_counter):
    """
    Copy JSONs from a single mod folder and rewrite priority sequentially.
    Returns updated priority_counter and appended log_lines list.
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


def find_priority_conflicts_for_folders(mods_dir, selected_folders_ordered):
    """
    Very small adaptation of your find_priority_conflicts:
    selected_folders_ordered - list of actual folder names (in load order)
    mods_dir - path to staging mods dir
    returns list of conflicts (folder_name, filename, priority, load_index, expected_position)
    """
    all_entries = []  # (mod_folder_name, file_name, priority, load_index)

    for load_index, folder in enumerate(selected_folders_ordered):
        mod_path = os.path.join(mods_dir, folder)
        if not os.path.exists(mod_path):
            continue
        try:
            jsons = collect_jsons(mod_path)
        except RuntimeError as e:
            # read errors will be surfaced by the caller; skip this mod
            continue
        for _, _, file_name, data, priority in jsons:
            all_entries.append((folder, file_name, priority, load_index))

    # sort by priority and find "jumps"
    all_entries_sorted = sorted(all_entries, key=lambda x: x[2])

    conflicts = []
    max_seen_load_index = -1
    for folder, file_name, priority, load_index in all_entries_sorted:
        if load_index < max_seen_load_index:
            conflicts.append((folder, file_name, priority, load_index, max_seen_load_index))
        else:
            max_seen_load_index = max(max_seen_load_index, load_index)
    return conflicts


# ==== Vortex parsing helpers ====

def recursive_find_entries(obj):
    """
    Recursively search decoded msgpack for a list of dicts that look like deployment entries
    (dicts with 'relPath' and/or 'source').
    Returns the first found list or None.
    """
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and ('relPath' in obj[0] or 'relpath' in obj[0]):
            return obj
        for item in obj:
            res = recursive_find_entries(item)
            if res:
                return res
    elif isinstance(obj, dict):
        # direct check
        for k, v in obj.items():
            res = recursive_find_entries(v)
            if res:
                return res
    return None


def recursive_find_key(obj, key_name):
    """Recursively find the first occurrence of key_name (case-sensitive) in nested dict/list structures."""
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
    """
    Load vortex.deployment.msgpack and return a dict:
      {
        "stagingPath": <str or None>,
        "targetPath": <str or None>,
        "entries": [ { 'relPath': ..., 'source': ..., ... }, ... ]
      }
    """
    if not os.path.exists(deployment_file):
        raise FileNotFoundError(f"Deployment file not found: {deployment_file}")

    with open(deployment_file, "rb") as f:
        data = msgpack.unpack(f, raw=False)

    # find entries list
    entries = None
    entries = recursive_find_entries(data)
    # fallback to some common keys
    if entries is None:
        entries = data.get("files") if isinstance(data, dict) else None
    if entries is None:
        entries = data.get("entries") if isinstance(data, dict) else None

    staging_path = recursive_find_key(data, "stagingPath")
    target_path = recursive_find_key(data, "targetPath")

    return {
        "stagingPath": staging_path,
        "targetPath": target_path,
        "entries": entries or []
    }


def extract_ordered_sources_from_entries(entries):
    """
    From deployment entries, return list of unique source names (in order of first appearance)
    but only from entries that touch OAR paths (relPath contains OpenAnimationReplacer).
    """
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
    """Lowercase, remove non-alnum, collapse spaces — used for fuzzy matching."""
    s = s or ""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def find_mod_folder_by_source(staging_mods_dir, source_name):
    """
    Try to find the actual mod folder name under staging_mods_dir that corresponds to source_name.
    Uses a few heuristics (direct match, canonical substring match).
    Returns folder name (not full path) or None.
    """
    if not os.path.isdir(staging_mods_dir):
        return None

    # list immediate subfolders only
    try:
        candidates = [d for d in os.listdir(staging_mods_dir) if os.path.isdir(os.path.join(staging_mods_dir, d))]
    except Exception:
        return None

    src_can = canonicalize_name(source_name)
    # try exact matches (case-insensitive)
    for c in candidates:
        if canonicalize_name(c) == src_can:
            return c

    # try containing: either candidate contains source or source contains candidate
    for c in candidates:
        cand_can = canonicalize_name(c)
        if cand_can in src_can or src_can in cand_can:
            return c

    # as last resort, try splitting source by '-' or '|'
    src_base = source_name.split("-")[0].strip()
    src_base_can = canonicalize_name(src_base)
    for c in candidates:
        if src_base_can and src_base_can in canonicalize_name(c):
            return c

    return None


# ==== GUI / main flow for Vortex ====

def append_log(window, text, newline=True):
    """Append text to GUI log and also print to console."""
    if newline:
        text = f"{text}"
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
    INPUT_WIDTH = 60
    LIST_HEIGHT = 20
    LOG_HEIGHT = 15

    layout = [
        [sg.Frame("Settings", [
            [sg.Text("Vortex deployment file (.msgpack):", size=(28, 1)),
             sg.InputText(key="DEPLOYMENT_FILE", size=(INPUT_WIDTH, 1)), sg.FileBrowse("Browse")],
            [sg.Text("Staging mods folder (optional):", size=(28, 1)),
             sg.InputText(key="STAGING_DIR", size=(INPUT_WIDTH, 1)), sg.FolderBrowse("Browse"),
             sg.Text("(leave empty to use stagingPath from deployment)")],
            [sg.Text("Output path:", size=(28, 1)),
             sg.InputText(key="OUTPUT_DIR", size=(INPUT_WIDTH, 1)), sg.FolderBrowse("Browse")],
            [sg.Text("Start priority:", size=(28, 1)),
             sg.InputText("1", key="START_PRIORITY", size=(10, 1))],
            [sg.Button("Load mods", size=(14, 1)), sg.Button("Check", size=(10, 1)),
             sg.Button("Run", button_color=("white", "green"), size=(10, 1)), sg.Button("Exit", size=(10, 1))]
        ], pad=(8, 8), element_justification='left', expand_x=True)],
        [sg.Frame("Detected OAR mods (deployment order):", [
            [sg.Listbox(values=[], select_mode=sg.LISTBOX_SELECT_MODE_MULTIPLE,
                       size=(INPUT_WIDTH, LIST_HEIGHT), key="MODS", expand_x=True)]
        ], pad=(8, 8), element_justification='left', expand_x=True)],
        [sg.Frame("Execution log", [
            [sg.Multiline(size=(INPUT_WIDTH, LOG_HEIGHT), key="LOG", autoscroll=True, disabled=True, expand_x=True)]
        ], pad=(8, 8), element_justification='left', expand_x=True)]
    ]
    return sg.Window("PriOARity — Vortex mode", layout, resizable=True)


def main():
    window = build_vortex_ui()
    deployment_data = None
    staging_root = None  # the folder containing mod folders
    entries = []
    mod_sources_ordered = []
    source_to_folder = {}  # mapping source_name -> folder_name (under staging_root)
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

            # figure staging mods dir
            user_staging = values["STAGING_DIR"].strip()
            staging_candidate = None
            if user_staging and os.path.isdir(user_staging):
                staging_candidate = user_staging
            else:
                stpath = deployment_data.get("stagingPath")
                if stpath and os.path.isdir(stpath):
                    # try common layout: stagingPath\mods, or stagingPath itself (mods may be direct children)
                    p1 = os.path.join(stpath, "mods")
                    if os.path.isdir(p1):
                        staging_candidate = p1
                    else:
                        staging_candidate = stpath

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

            # If we have staging_root, try to map sources to actual folder names
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

            # update listbox
            display_list = []
            for src in mod_sources_ordered:
                mapped = f" [{source_to_folder[src]}]" if src in source_to_folder else ""
                display_list.append(f"{src}{mapped}")
            window["MODS"].update(display_list)
            append_log(window, "Load complete. You can select mods and click Check or Run.")

        if event == "Check":
            selected_items = values["MODS"]
            if not selected_items:
                sg.popup_error("No mods selected in the list.")
                continue

            # the listbox contains strings like "SourceName [folder]" or "SourceName"
            selected_sources = []
            for item in selected_items:
                # strip appended mapping if present
                if "]" in item and "[" in item:
                    # split at the last " ["
                    src = item.rsplit(" [", 1)[0]
                else:
                    src = item
                selected_sources.append(src)

            # build ordered list of actual folder names (if mapping exists)
            selected_folders_ordered = []
            unmapped = []
            for src in selected_sources:
                folder = source_to_folder.get(src)
                if folder:
                    selected_folders_ordered.append(folder)
                else:
                    unmapped.append(src)

            if unmapped:
                append_log(window, f"Warning: some selected sources were not mapped to staging folders and will be skipped: {unmapped}")

            if not selected_folders_ordered:
                append_log(window, "No mapped folders found for selected mods, cannot run conflict check.")
                continue

            append_log(window, f"Running conflict check on {len(selected_folders_ordered)} mapped folders...")
            conflicts = find_priority_conflicts_for_folders(staging_root, selected_folders_ordered)

            log_lines = []
            if conflicts:
                log_lines.append("Priority conflicts detected:")
                # conflicts contain folder names; map back to source display if possible
                folder_to_source = {v: k for k, v in source_to_folder.items()}
                for folder, file_name, priority, load_index, expected in conflicts:
                    srcname = folder_to_source.get(folder, folder)
                    log_lines.append(f"- Source '{srcname}' (folder '{folder}'), File: {file_name}, Priority: {priority}, "
                                     f"Load index: {load_index} (should be >= {expected})")
            else:
                log_lines.append("No conflicts detected!")

            # update GUI log and highlight warning sign in listbox
            window["LOG"].update("\n".join(log_lines))
            # highlight: replace display entries that correspond to conflict folders
            conflict_folders = set(c[0] for c in conflicts)
            # rebuild display list keeping mapping suffix
            new_display = []
            for src in mod_sources_ordered:
                mapped = source_to_folder.get(src)
                suffix = f" [{mapped}]" if mapped else ""
                if mapped in conflict_folders:
                    new_display.append(f"⚠ {src}{suffix}")
                else:
                    new_display.append(f"{src}{suffix}")
            window["MODS"].update(new_display)
            append_log(window, "Conflict check finished.")

        if event == "Run":
            selected_items = values["MODS"]
            output_dir = values["OUTPUT_DIR"]
            if not selected_items:
                sg.popup_error("No mods selected in the list.")
                continue
            if not output_dir:
                sg.popup_error("Please select a valid output directory.")
                continue
            try:
                start_priority = int(values["START_PRIORITY"])
                if start_priority < 1:
                    raise ValueError
            except Exception:
                sg.popup_error("Start priority must be integer (>=1).")
                continue

            # prepare selected sources and map to folders
            selected_sources = []
            for item in selected_items:
                if "]" in item and "[" in item:
                    src = item.rsplit(" [", 1)[0]
                else:
                    src = item
                selected_sources.append(src)

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

            # create output root
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
                    priority_counter = copy_jsons_from_mod(mod_folder_path, out_root, src, start_priority, log_lines, priority_counter)
                except RuntimeError as e:
                    append_log(window, f"Error processing '{src}': {e}")

            # show and save log
            log_text = "\n".join(log_lines) if log_lines else "(no json files found / nothing processed)"
            window["LOG"].update(log_text)
            # write log file
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
