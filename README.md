Description

PriOARity is a small utility for Mod Organizer 2.
It allows you to renumber animation priorities across multiple mods automatically, preventing conflicts and ensuring animation mods use your load order as source of priority.

Simple usage example:
You have two mods:
Mod 1 – a large combat animation pack with priorities in the range 90000–90100
Mod 2 – a specific moveset with priorities in the range 80100–80120
Both mods operate under the same conditions.
Regardless of the load order in MO2, the second mod would have lower priority than the first, and its animations would not play.
With PriOARity, you can create new configuration files for both mods, allowing you to renumber them sequentially, for example in the range 90200–90220.

Key points:
Works directly with your MO2 profile — detects all OAR-enabled mods.
Lets you select which mods to process.
Customizable starting priority for animation numbering.
Creates a safe output folder with renumbered JSON configs — your original mods are never modified.
Generates a detailed log file for tracking changes.

Usage
Launch P﻿riOARity.exe.
Select your MO2 profile folder.
Choose an output folder for processed JSON files.
Set the starting priority (default is 1).
Click Load mods — the tool will list all detected OAR mods.
Select mods to process.
Click Run — JSON configs will be copied and renumbered in the output folder.
Review the execution log for details.
Add output folder to archive.
Drag&drop archive to Mod Organizer mod list.

This tool was created with assistance from ChatGPT.
Feel free to report any bugs.
