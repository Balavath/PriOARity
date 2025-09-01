import os
import json
import FreeSimpleGUI as sg
from datetime import datetime

# Возможные варианты пути к OAR в моде
OAR_SUBPATHS = [
    os.path.join("meshes", "actors", "character", "animations", "OpenAnimationReplacer")
]

# ==== Функции ====
def get_mod_order(modlist_file):
    mods = []
    with open(modlist_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if line[0] == "+":
                    mods.append(line[1:].strip())
    return mods

def is_oar_mod(mod_path):
    """Проверяет, содержит ли мод OAR-анимации (несколько вариантов пути)."""
    for subpath in OAR_SUBPATHS:
        full_path = os.path.join(mod_path, subpath)
        print(f"Check OAR: {full_path}")
        if os.path.exists(full_path):
            print("→ found!")
            return True
    return False

def collect_jsons(mod_path):
    entries = []
    for root, _, files in os.walk(mod_path):
        rel_path = os.path.relpath(root, mod_path)
        for file in files:
            if file.lower().endswith(".json"):
                src_file = os.path.join(root, file)
                try:
                    with open(src_file, encoding="utf-8") as f:
                        data = json.load(f)
                    old_pri = data.get("priority", 0)
                except Exception as e:
                    print(f"Read error {src_file}: {e}")
                    continue
                entries.append((src_file, rel_path, file, data, old_pri))
    entries.sort(key=lambda x: x[4])  # сортировка по priority
    return entries

def copy_and_rewrite(mods, mods_dir, out_dir, selected_mods, start_priority=1):
    priority_counter = start_priority
    log_lines = []
    for mod in mods:
        if mod not in selected_mods:
            continue

        mod_path = os.path.join(mods_dir, mod)
        if not os.path.exists(mod_path):
            continue

        jsons = collect_jsons(mod_path)
        for src_file, rel_path, file, data, old_pri in jsons:
            target_root = os.path.join(out_dir, rel_path)
            os.makedirs(target_root, exist_ok=True)
            dst_file = os.path.join(target_root, file)

            if "priority" in data:
                data["priority"] = priority_counter
                log_lines.append(f"[{mod}] {src_file} : {old_pri} → {priority_counter}")
                priority_counter += 1

            with open(dst_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    return log_lines

# ==== UI ====
def main():
    sg.ChangeLookAndFeel("DarkGrey9")

    LIST_HEIGHT = 20
    LOG_HEIGHT = 15
    INPUT_WIDTH = 50  # ширина всех полей ввода

    layout = [
        [sg.Frame("Settings", [
            [sg.Text("MO2 Profile path:", size=(20,1)), sg.InputText(key="PROFILE", size=(INPUT_WIDTH,1)), sg.FolderBrowse("Browse")],
            [sg.Text("Output path:", size=(20,1)), sg.InputText(key="OUTPUT_DIR", size=(INPUT_WIDTH,1)), sg.FolderBrowse("Browse")],
            [sg.Text("Start priority:", size=(20,1)), sg.InputText("1", key="START_PRIORITY", size=(10,1))],
            [sg.Button("Load mods", size=(20,1))]
        ], pad=(10,10), element_justification='left', expand_x=True)],

        [sg.Frame("OAR List", [
            [sg.Listbox(values=[], select_mode=sg.LISTBOX_SELECT_MODE_MULTIPLE, size=(INPUT_WIDTH, LIST_HEIGHT), key="MODS", expand_x=True)]
        ], pad=(10,10), element_justification='left', expand_x=True)],

        [sg.Frame("Actions", [
            [sg.Button("Run", button_color=("white","green"), size=(10,1)), sg.Button("Exit", size=(10,1))]
        ], pad=(10,10), element_justification='center', expand_x=True)],

        [sg.Frame("Execution log", [
            [sg.Multiline(size=(INPUT_WIDTH, LOG_HEIGHT), key="LOG", autoscroll=True, disabled=True, expand_x=True)]
        ], pad=(10,10), element_justification='left', expand_x=True)]
    ]

    window = sg.Window("PriOARity — OAR Priority Tool", layout, resizable=True)

    mods = []
    mods_dir = ""
    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Exit"):
            break

        if event == "Load mods":
            profile = values["PROFILE"]
            modlist_file = os.path.join(profile, "modlist.txt")
            # правильный путь к папке mods рядом с profiles
            mods_dir = os.path.abspath(os.path.join(profile, "..", "..", "mods"))
            print(f"Folder for mods: {mods_dir}")

            if not os.path.exists(modlist_file):
                sg.popup_error("modlist.txt not found in the specified profile folder.")
                continue
            if not os.path.exists(mods_dir):
                sg.popup_error(f"mods folder not found: {mods_dir}")
                continue

            # читаем все строки
            with open(modlist_file, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            total_mods = len(lines)
            active_mods = [line[1:].strip() for line in lines if line.startswith("+")]
            disabled_mods = [line[1:].strip() for line in lines if line.startswith("-")]

            # реверсируем, чтобы первый в списке был загружен последним
            active_mods.reverse()

            mods = [m for m in active_mods if is_oar_mod(os.path.join(mods_dir, m))]

            # обновляем список в UI
            window["MODS"].update(mods)

            # подробный лог
            log_text = (
                f"Summary items in modlist.txt: {total_mods}\n"
                f"Active mods (+): {len(active_mods)}\n"
                f"Disabled mods (-): {len(disabled_mods)}\n"
                f"OAR-mods: {len(mods)}\n\n"
                f"OAR-mods list:\n" + "\n".join(mods)
            )
            window["LOG"].update(log_text)
            sg.popup(f"{len(mods)} OAR-mods loaded.")

        if event == "Run":
            selected = values["MODS"]
            output_dir = values["OUTPUT_DIR"]
            if not output_dir or not os.path.exists(output_dir):
                sg.popup_error("Please select a valid output folder!")
                continue
            if not selected:
                sg.popup_error("Mods are not selected!")
                continue

            try:
                start_priority = int(values["START_PRIORITY"])
                if start_priority < 1:
                    raise ValueError
            except ValueError:
                sg.popup_error("Start priority must be integer (>= 1)")
                continue
            output_dir = os.path.join(output_dir, "PriOARity_Output")
            log = copy_and_rewrite(mods, mods_dir, output_dir, set(selected), start_priority)
            log_text = "\n".join(log)
            window["LOG"].update(log_text)

            # пишем лог в файл
            log_file = os.path.join(output_dir, f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(log_text)
            sg.popup(f"Done! Log saved to file {log_file}.")

    window.close()

if __name__ == "__main__":
    main()
