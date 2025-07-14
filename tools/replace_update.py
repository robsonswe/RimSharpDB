import json
from pathlib import Path
import sys
import asyncio
import re # Keep regex for version parsing, if needed by db.json versions
from typing import Dict, Any, Optional, List, Tuple

import threading
import queue

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- Configuration ---
ROOT_DIR = Path(__file__).resolve().parent.parent
DB_DIR = ROOT_DIR / "db"

REPLACEMENTS_JSON_FILE = DB_DIR / "replacements.json"
DB_JSON_FILE = DB_DIR / "db.json"


# --- Helper Functions (No more scraping-related regex) ---
def get_version_key(version_str: str) -> Tuple[int, ...]:
    try: return tuple(map(int, version_str.split('.')))
    except (ValueError, AttributeError): return (0,)

# --- Global DB Data Cache ---
# Loads db.json once at startup and flattens it for easy lookup by SteamId
_GLOBAL_DB_DATA_BY_STEAMID: Dict[str, Dict[str, Any]] = {}
def _load_and_flatten_db_json():
    global _GLOBAL_DB_DATA_BY_STEAMID
    if not DB_JSON_FILE.exists():
        messagebox.showwarning("DB File Missing", f"'{DB_JSON_FILE.name}' not found. Please ensure it exists and is updated by the 'db_updater.py' script.")
        return
    try:
        with open(DB_JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "mods" in data and isinstance(data["mods"], dict):
                for package_id, steam_ids_dict in data["mods"].items():
                    if isinstance(steam_ids_dict, dict):
                        for steam_id, details in steam_ids_dict.items():
                            if steam_id.isdigit(): # Ensure it's a valid Steam ID
                                _GLOBAL_DB_DATA_BY_STEAMID[steam_id] = {
                                    "mod_id": package_id, # This is the packageId from db.json
                                    "name": details.get("name", "Unknown Name"),
                                    "versions": [v.strip() for v in details.get("versions", []) if isinstance(v, str)], # Ensure versions are stripped strings
                                    "authors": [a.strip() for a in details.get("authors", "").split(',') if a.strip()], # Authors from db.json
                                    "published": details.get("published", False) # True means published/valid, False means error/unpublished
                                }
    except (json.JSONDecodeError, IOError) as e:
        messagebox.showerror("DB Load Error", f"Error loading '{DB_JSON_FILE.name}': {e}\nPlease check its format.")
        _GLOBAL_DB_DATA_BY_STEAMID = {} # Reset to empty on error

_load_and_flatten_db_json() # Load DB on script start

# --- ModInfo Class ---
class ModInfo:
    def __init__(self, steam_id: str):
        self.steam_id = steam_id; self.name: Optional[str] = None; self.authors: List[str] = []
        self.mod_id: Optional[str] = None; self.versions: List[str] = []; self.source: str = "N/A"
        self.is_valid_on_steam: bool = False # Now based on db.json's 'published' status

# --- Main Application ---
class ModReplacerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Mod Replacement Manager")
        self.root.geometry("900x550")
        self.queue = queue.Queue()
        self.original_mod: Optional[ModInfo] = None
        self.replacement_mod: Optional[ModInfo] = None
        self.managing_existing_relationship = False 
        
        main_frame = ttk.Frame(root, padding="10"); main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1); main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(0, weight=1)

        self.original_panel = self._create_panel(main_frame, "Mod to be Replaced (Original)", "original")
        self.replacement_panel = self._create_panel(main_frame, "Replacement Mod", "replacement")
        self.original_panel["frame"].grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.replacement_panel["frame"].grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        bottom_frame = ttk.Frame(main_frame, padding="10 0 0 0"); bottom_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=10)
        self.action_button = ttk.Button(bottom_frame, text="Add to JSON", command=self.add_replacement_to_json, state="disabled")
        self.action_button.pack(side=tk.LEFT, fill=tk.X, expand=True)
        rules_frame = ttk.LabelFrame(bottom_frame, text="Validation Rules"); rules_frame.pack(side=tk.LEFT, padx=(20, 0))
        self.rule1_var = tk.StringVar(value="✗ Both mods must be valid"); # Rule 2 will be removed from here
        self.rule3_var = tk.StringVar(value="✗ Is a new relationship"); self.rule4_var = tk.StringVar(value="✗ Replacement is up-to-date")
        ttk.Label(rules_frame, textvariable=self.rule1_var).pack(anchor="w"); 
        # ttk.Label(rules_frame, textvariable=self.rule2_var).pack(anchor="w") # Rule 2 removed
        self.rule3_label = ttk.Label(rules_frame, textvariable=self.rule3_var) # Storing for later update
        self.rule3_label.pack(anchor="w")
        self.rule4_label = ttk.Label(rules_frame, textvariable=self.rule4_var) # Storing for later update
        self.rule4_label.pack(anchor="w")
        
        self.process_queue()

    def _create_panel(self, parent, title: str, panel_type: str) -> Dict[str, Any]:
        panel_frame = ttk.LabelFrame(parent, text=title, padding="10")
        entry_frame = ttk.Frame(panel_frame); entry_frame.pack(fill=tk.X)
        entry_frame.columnconfigure(0, weight=1)
        steam_id_var = tk.StringVar()
        entry = ttk.Entry(entry_frame, textvariable=steam_id_var)
        fetch_button = ttk.Button(entry_frame, text="Fetch Info", command=lambda: self.fetch_mod_info_thread(panel_type, steam_id_var.get()))
        entry.grid(row=0, column=0, sticky="ew"); fetch_button.grid(row=0, column=1, padx=(5, 0))
        info_grid = ttk.Frame(panel_frame, padding="0 10 0 0"); info_grid.pack(fill=tk.BOTH, expand=True, pady=5)
        info_grid.columnconfigure(1, weight=1)
        
        # mod_id_entry_var and mod_id_valid_var are no longer needed for input
        vars_dict = { "source_var": tk.StringVar(value="N/A"), "name_var": tk.StringVar(value="N/A"), "author_var": tk.StringVar(value="N/A"), "versions_var": tk.StringVar(value="N/A"), "mod_id_display_var": tk.StringVar(),}
        
        ttk.Label(info_grid, text="Source:").grid(row=0, column=0, sticky="w"); ttk.Label(info_grid, textvariable=vars_dict["source_var"]).grid(row=0, column=1, sticky="w")
        ttk.Label(info_grid, text="Name:").grid(row=1, column=0, sticky="w"); ttk.Label(info_grid, textvariable=vars_dict["name_var"], wraplength=300).grid(row=1, column=1, sticky="w")
        ttk.Label(info_grid, text="Authors:").grid(row=2, column=0, sticky="w"); ttk.Label(info_grid, textvariable=vars_dict["author_var"], wraplength=300, anchor="w", justify=tk.LEFT).grid(row=2, column=1, sticky="w")
        ttk.Label(info_grid, text="Versions:").grid(row=3, column=0, sticky="w"); ttk.Label(info_grid, textvariable=vars_dict["versions_var"], wraplength=300).grid(row=3, column=1, sticky="w")
        
        mod_id_label = ttk.Label(info_grid, text="ModId:")
        mod_id_display = ttk.Label(info_grid, textvariable=vars_dict["mod_id_display_var"], foreground="navy")
        
        # Only show the display label for ModId now
        mod_id_label.grid(row=4, column=0, sticky="w")
        mod_id_display.grid(row=4, column=1, sticky="w")
        
        return {"frame": panel_frame, "id_var": steam_id_var, "id_entry": entry, "fetch_button": fetch_button, "vars": vars_dict, "ui_elements": {"mod_id_label": mod_id_label, "mod_id_display": mod_id_display}} # Simplified ui_elements

    def fetch_mod_info_thread(self, panel_type: str, steam_id: str):
        if not steam_id.isdigit(): messagebox.showerror("Invalid ID", "Steam ID must be a number."); return
        
        # Reset current panel's UI only if not managing an existing relationship
        # Or if managing and it's the replacement panel (which is always editable)
        if not self.managing_existing_relationship or panel_type == 'replacement':
            self._reset_panel_ui(panel_type)
        
        # The managing_existing_relationship flag is set by async_fetch_worker.
        threading.Thread(target=run_async_worker, args=(async_fetch_worker, self.queue, panel_type, steam_id), daemon=True).start()

    def process_queue(self):
        try:
            while True:
                msg_type, data = self.queue.get_nowait()
                if "info" in msg_type: messagebox.showinfo(msg_type.split('_')[0].title(), data)
                elif "success" in msg_type:
                    panel_type = data.pop('panel_type') 
                    mod_info = ModInfo(data['steam_id']); mod_info.name = data.get('name'); mod_info.authors = data.get('authors', [])
                    mod_info.versions = data.get('versions', []); mod_info.mod_id = data.get('mod_id'); mod_info.source = data.get('source', "N/A")
                    mod_info.is_valid_on_steam = data.get('is_valid_on_steam', False) # From db.json published status
                    if panel_type == 'original': self.original_mod = mod_info
                    else: self.replacement_mod = mod_info
                    self._update_panel_ui(panel_type, mod_info)

                    if data.get('is_existing_relationship_load', False):
                        self.managing_existing_relationship = True

                elif "failure" in msg_type:
                    panel_type = msg_type.split('_')[0]
                    if panel_type == 'original': self.original_mod = None
                    else: self.replacement_mod = None
                    panel = self.original_panel if panel_type == 'original' else self.replacement_panel
                    panel["vars"]["source_var"].set("Not Found in DB")
                    # Special message for DB not found
                    messagebox.showerror("Mod Not Found", f"Steam ID {data['steam_id']} not found in '{DB_JSON_FILE.name}'.\nPlease ensure the mod is installed and run 'updatetags.py' to update your database.")
                
                self._handle_post_fetch_logic()
        except queue.Empty: pass
        finally: self.root.after(100, self.process_queue)

    def _handle_post_fetch_logic(self):
        # Apply locking/unlocking based on whether we're managing an existing entry
        if self.managing_existing_relationship:
            self._lock_panel('original')
        else:
            self._unlock_all_panels()

        # Perform updates/cleanup and set button mode ONLY when both panels have valid mods.
        if self.original_mod and self.original_mod.is_valid_on_steam and \
           self.replacement_mod and self.replacement_mod.is_valid_on_steam:
            
            replacements = load_replacements_file().get("mods", {})
            orig_id, repl_id = self.original_mod.steam_id, self.replacement_mod.steam_id
            
            existing_entry_key = find_relationship_key_strict(orig_id, repl_id, replacements)

            if existing_entry_key:
                # Removed auto-update/cleanup logic from here, as per requirements.
                pass
        
        self.validate_rules()

    def _update_panel_ui(self, panel_type: str, mod_info: ModInfo):
        panel = self.original_panel if panel_type == 'original' else self.replacement_panel
        panel["id_var"].set(mod_info.steam_id)
        panel["vars"]["source_var"].set(mod_info.source)
        panel["vars"]["name_var"].set(mod_info.name or "N/A")
        panel["vars"]["author_var"].set(", ".join(mod_info.authors) or "N/A")
        panel["vars"]["versions_var"].set(", ".join(mod_info.versions) or "N/A")
        ui = panel["ui_elements"]
        
        ui["mod_id_label"].grid(row=4, column=0, sticky="w")
        display_value = mod_info.mod_id if mod_info.mod_id else "(none)"
        panel["vars"]["mod_id_display_var"].set(display_value)
        ui["mod_id_display"].grid(row=4, column=1, sticky="w") # Always display ModId

    def _reset_panel_ui(self, panel_type: str):
        panel = self.original_panel if panel_type == 'original' else self.replacement_panel
        if panel_type == 'original': self.original_mod = None
        else: self.replacement_mod = None
        for key, var in panel["vars"].items(): var.set("N/A" if "var" in key else "")
        panel["id_var"].set("") # Clear Steam ID entry field
        # ModId display label is always there now, no need to grid_remove/add
        panel["vars"]["mod_id_display_var"].set("") # Clear its content
        
    def reset_all_ui(self):
        self._unlock_all_panels()
        self._reset_panel_ui("original"); self._reset_panel_ui("replacement")
        self.managing_existing_relationship = False
        self.validate_rules()

    def _lock_panel(self, panel_type):
        panel = self.original_panel if panel_type == 'original' else self.replacement_panel
        panel["id_entry"].config(state='disabled'); panel["fetch_button"].config(state='disabled')
    
    def _unlock_all_panels(self):
        for panel in [self.original_panel, self.replacement_panel]:
            panel["id_entry"].config(state='normal'); panel["fetch_button"].config(state='normal')

    def validate_rules(self):
        orig_ok = self.original_mod is not None and self.original_mod.is_valid_on_steam
        repl_ok = self.replacement_mod is not None and self.replacement_mod.is_valid_on_steam
        rule1_ok = orig_ok and repl_ok
        self.rule1_var.set("✓ Both mods are valid" if rule1_ok else "✗ Both mods must be valid")
        
        # Rule 2 (ModId validation) is now removed, as it's not user input
        
        rule3_ok, rule4_ok = False, False
        button_mode = "add"
        
        if self.original_mod and self.replacement_mod: # Only validate relationship rules if both mods are loaded
            replacements = load_replacements_file().get("mods", {})
            
            # Rule 3: Is a new relationship? (Unidirectional check)
            # It's "new" if the original mod's Steam ID is NOT ALREADY A KEY in the replacements file.
            if self.original_mod.steam_id in replacements:
                rule3_ok = False # Original mod is already involved in a relationship (as a key)
            else:
                rule3_ok = True # Original mod is new, so this is potentially a new relationship
            
            # Rule 4: Replacement is up-to-date
            max_orig_ver = max(self.original_mod.versions, key=get_version_key) if self.original_mod.versions else "0"
            max_repl_ver = max(self.replacement_mod.versions, key=get_version_key) if self.replacement_mod.versions else "0"
            rule4_ok = get_version_key(max_repl_ver) >= get_version_key(max_orig_ver)

            # Determine button mode based on existing relationship and loaded mods
            existing_exact_relationship_loaded = (self.original_mod.steam_id in replacements and 
                                                  replacements[self.original_mod.steam_id].get("ReplacementSteamId") == self.replacement_mod.steam_id)

            if existing_exact_relationship_loaded:
                button_mode = "view/remove" # The exact pair is loaded, allow removal
            elif self.original_mod.steam_id in replacements:
                # Original mod exists (as a key), but this replacement is different
                button_mode = "change"
            else:
                # Original mod is not a key in replacements, so it's a new entry
                button_mode = "add"

        self.rule3_label.config(text="✓ Is a new relationship" if rule3_ok else "✗ Relationship already exists") # Update label directly
        self.rule4_label.config(text="✓ Replacement is up-to-date" if rule4_ok else "✗ Replacement is up-to-date") # Update label directly
        
        # Update button based on mode and overall validation status
        if button_mode == "view/remove":
            self.action_button.config(text="Remove From JSON", command=self.remove_entry_from_json, state="normal")
        elif button_mode == "change":
            # Rule 2 is no longer a factor here, so it's `rule1_ok and rule4_ok`
            self.action_button.config(text="Change Replacement", command=self.change_replacement_in_json, state="normal" if rule1_ok and rule4_ok else "disabled")
        else: # "add"
            # Rule 2 is no longer a factor here, so it's `rule1_ok and rule3_ok and rule4_ok`
            self.action_button.config(text="Add to JSON", command=self.add_replacement_to_json, state="normal" if all([rule1_ok, rule3_ok, rule4_ok]) else "disabled")

    def add_replacement_to_json(self):
        # ModId is read-only, no need to finalize from user input
        replacements = load_replacements_file();
        if "mods" not in replacements: replacements["mods"] = {}
        orig, repl = self.original_mod, self.replacement_mod
        new_entry = self._create_json_entry(orig, repl)
        replacements["mods"][orig.steam_id] = new_entry
        save_replacements_file(replacements); messagebox.showinfo("Success", f"Replacement for '{orig.name}' has been added."); self.reset_all_ui()

    def remove_entry_from_json(self):
        if not self.original_mod: return
        if not messagebox.askyesno("Confirm Removal", f"Are you sure you want to remove the replacement entry for '{self.original_mod.name}'?"): return
        
        replacements = load_replacements_file()
        key_to_delete = self.original_mod.steam_id
        if "mods" in replacements and key_to_delete in replacements["mods"]:
            del replacements["mods"][key_to_delete]; save_replacements_file(replacements)
            messagebox.showinfo("Success", f"Entry for '{self.original_mod.name}' has been removed.")
        self.reset_all_ui()

    def change_replacement_in_json(self):
        if not messagebox.askyesno("Confirm Change", f"Are you sure you want to change the replacement for '{self.original_mod.name}' to '{self.replacement_mod.name}'?"): return
        # ModId is read-only, no need to finalize from user input
        replacements = load_replacements_file()
        orig, repl = self.original_mod, self.replacement_mod
        key_to_update = orig.steam_id
        
        # Ensure we're updating an existing entry
        if "mods" in replacements and key_to_update in replacements["mods"]:
            replacements["mods"][key_to_update] = self._create_json_entry(orig, repl)
            save_replacements_file(replacements)
            messagebox.showinfo("Success", f"The replacement for '{orig.name}' has been changed to '{repl.name}'.")
        self.reset_all_ui()

    # _finalize_mod_ids is no longer needed as ModId is read-only
    
    def _create_json_entry(self, orig, repl):
        return { "Author": ", ".join(orig.authors), "ModId": orig.mod_id or "", "ModName": orig.name, "Versions": ",".join(orig.versions), "SteamId": orig.steam_id, "ReplacementAuthor": ", ".join(repl.authors), "ReplacementModId": repl.mod_id or "", "ReplacementName": repl.name, "ReplacementSteamId": repl.steam_id, "ReplacementVersions": ",".join(repl.versions),}


def run_async_worker(async_func, q, *args):
    try:
        if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(async_func(q, *args))
    except Exception as e:
        # Catch any unexpected errors in the worker and send a general failure message
        q.put(("failure", {"panel_type": "unknown", "steam_id": args[-1], "error": str(e)}))

# Renamed _fetch_and_combine to reflect its new purpose: fetching from DB only
async def get_mod_info_from_db(steam_id: str) -> Optional[Dict]:
    mod_data = _GLOBAL_DB_DATA_BY_STEAMID.get(steam_id)
    if mod_data:
        # Map db.json fields to ModInfo fields
        return {
            "steam_id": steam_id,
            "name": mod_data.get("name"),
            "authors": mod_data.get("authors"),
            "mod_id": mod_data.get("mod_id"),
            "versions": mod_data.get("versions"),
            "source": "DB.json",
            "is_valid_on_steam": mod_data.get("published", False)
        }
    return None

async def async_fetch_worker(q: queue.Queue, clicked_panel_type: str, steam_id: str):
    replacements = load_replacements_file().get("mods", {})
    
    # Try to find a relationship where this SteamID is the ORIGINAL mod
    relationship_as_original = get_relationship_info_from_json_as_original(steam_id, replacements)

    if relationship_as_original:
        # Case 1: User entered an ID that IS an ORIGINAL in an existing relationship.
        # Load the ENTIRE relationship (original and its specific replacement)
        orig_id, repl_id = relationship_as_original

        # Fetch data for the original mod and send to the original panel
        original_mod_data = await get_mod_info_from_db(orig_id)
        if original_mod_data:
            q.put(("success", {**original_mod_data, 'panel_type': 'original', 'is_existing_relationship_load': True}))
        else:
            q.put(("failure", {'panel_type': 'original', 'steam_id': orig_id}))

        # Fetch data for the replacement mod and send to the replacement panel
        replacement_mod_data = await get_mod_info_from_db(repl_id)
        if replacement_mod_data:
            q.put(("success", {**replacement_mod_data, 'panel_type': 'replacement', 'is_existing_relationship_load': True}))
        else:
            q.put(("failure", {'panel_type': 'replacement', 'steam_id': repl_id}))

    else:
        # Case 2: User entered an ID that is NOT an ORIGINAL in an existing relationship.
        # This could be a new mod, or a replacement mod that is already replacing other originals.
        # We only load the clicked mod into its respective panel.
        primary_data = await get_mod_info_from_db(steam_id)
        if primary_data:
            q.put((f"{clicked_panel_type}_success", {**primary_data, 'panel_type': clicked_panel_type, 'is_existing_relationship_load': False}))
        else:
            q.put((f"{clicked_panel_type}_failure", {'panel_type': clicked_panel_type, 'steam_id': steam_id}))


def get_relationship_info_from_json_as_original(steam_id: str, replacements: Dict) -> Optional[Tuple[str, str]]:
    """
    Checks if the given steam_id exists as an ORIGINAL mod (key) in the JSON.
    Returns (original_steam_id, replacement_steam_id) if found, else None.
    This makes detection strictly unidirectional for loading.
    """
    if steam_id in replacements: # steam_id is an original
        return steam_id, replacements[steam_id].get("ReplacementSteamId")
    return None


def find_relationship_key_strict(id1, id2, replacements):
    """
    Finds the original_steam_id key for a specific relationship (id1 -> id2).
    Returns the original_steam_id key if the exact relationship is found.
    """
    if id1 in replacements and replacements[id1].get("ReplacementSteamId") == id2:
        return id1
    return None

def fetch_from_json_file(steam_id: str) -> Optional[Dict]:
    """
    This function is now ONLY used to check `replacements.json` for *relationship info*,
    not for mod details (which come from _GLOBAL_DB_DATA_BY_STEAMID).
    It checks if a steam_id exists as an original or a replacement in any entry
    to help in `_fetch_and_combine` (though _fetch_and_combine is simplified now).
    It returns raw dictionary from replacements.json.
    """
    data = load_replacements_file().get("mods", {})
    if steam_id in data: # Check if it's an original mod
        return data[steam_id]
    for mod_data in data.values(): # Check if it's a replacement mod
        if mod_data.get("ReplacementSteamId") == steam_id:
            return mod_data
    return None


def load_replacements_file() -> Dict:
    if not REPLACEMENTS_JSON_FILE.exists(): return {"mods": {}}
    try:
        with open(REPLACEMENTS_JSON_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except (json.JSONDecodeError, IOError): return {"mods": {}}

def save_replacements_file(data: Dict):
    with open(REPLACEMENTS_JSON_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)

def validate_mod_id_string(mod_id: str) -> Tuple[bool, str]:
    # This function is technically not used anymore for user input ModId validation
    # but kept for completeness if needed in future.
    mod_id = mod_id.strip()
    if len(mod_id) < 3: return False, "Min 3 chars"
    if " " in mod_id: return False, "No spaces allowed"
    if "." not in mod_id: return False, "Must contain a dot"
    if not mod_id[0].isalnum(): return False, "Start/end must be alphanumeric"
    if not mod_id[-1].isalnum(): return False, "Start/end must be alphanumeric"
    return True, ""

if __name__ == "__main__":
    app_root = tk.Tk()
    app = ModReplacerApp(app_root)
    app_root.mainloop()