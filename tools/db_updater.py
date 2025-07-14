import os
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import sys
import asyncio
import aiohttp
import re
from typing import List, Dict, Any, Optional, Tuple

import threading
import queue

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

# --- Configuration (Constants) ---
API_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
MAX_CONCURRENT_REQUESTS = 10
VERSION_REGEX = re.compile(r"^\d+\.\d+(\.\d+)?$")
DB_JSON_FILE = Path(__file__).resolve().parent.parent / "db" / "db.json"
BATCH_SIZE = 10

# --- New Helper Function for Version Comparison ---
def get_version_key(version_str: str) -> Tuple[int, ...]:
    """Converts a version string '1.5.2' into a tuple (1, 5, 2) for correct comparison."""
    try:
        return tuple(map(int, version_str.split('.')))
    except (ValueError, AttributeError):
        # Return a low value for un-parseable strings so they are never considered 'highest'
        return (0,)

class ModUpdaterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RimWorld Mod Database Updater v3")
        self.root.geometry("800x600")

        self.mods_dir = None
        self.update_thread = None
        self.queue = queue.Queue()

        # --- GUI Setup (No changes in this section) ---
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        controls_frame = ttk.Frame(self.main_frame)
        controls_frame.pack(fill=tk.X, pady=5)
        controls_frame.columnconfigure(1, weight=1)
        ttk.Label(controls_frame, text="Mods Folder:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.folder_path_var = tk.StringVar(value="No folder selected")
        ttk.Entry(controls_frame, textvariable=self.folder_path_var, state="readonly").grid(row=0, column=1, sticky="ew")
        self.select_folder_button = ttk.Button(controls_frame, text="Select Folder...", command=self.select_mod_folder)
        self.select_folder_button.grid(row=0, column=2, padx=(5, 0))
        self.start_button = ttk.Button(controls_frame, text="Start Update", command=self.start_update_process, state="disabled")
        self.start_button.grid(row=1, column=0, columnspan=3, pady=(10, 0), sticky="ew")
        progress_frame = ttk.Frame(self.main_frame)
        progress_frame.pack(fill=tk.X, pady=10)
        progress_frame.columnconfigure(0, weight=1)
        ttk.Label(progress_frame, text="Local Scan Progress:").grid(row=0, column=0, sticky="w")
        self.scan_progress = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.scan_progress.grid(row=1, column=0, sticky="ew")
        ttk.Label(progress_frame, text="Steam API Fetch Progress:").grid(row=2, column=0, sticky="w", pady=(5,0))
        self.api_progress = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.api_progress.grid(row=3, column=0, sticky="ew")
        log_frame = ttk.LabelFrame(self.main_frame, text="Log Output", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_area = scrolledtext.ScrolledText(log_frame, state="disabled", wrap=tk.WORD, bg="#f0f0f0")
        self.log_area.pack(fill=tk.BOTH, expand=True)

    def select_mod_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            if any(p.name.isdigit() and p.is_dir() for p in Path(folder_selected).iterdir()):
                self.mods_dir = Path(folder_selected)
                self.folder_path_var.set(str(self.mods_dir))
                self.start_button.config(state="normal")
                self.log_message(f"Selected mods folder: {self.mods_dir}")
            else:
                self.log_message("Warning: The selected folder does not appear to contain mod subdirectories.", "error")
                tk.messagebox.showwarning("Invalid Folder", "The selected folder doesn't seem to contain any mods (no numbered subdirectories found). Please select the correct folder.")

    def start_update_process(self):
        if not self.mods_dir:
            tk.messagebox.showerror("Error", "No mods folder selected.")
            return
        self.start_button.config(state="disabled")
        self.select_folder_button.config(state="disabled")
        self.log_area.config(state="normal")
        self.log_area.delete("1.0", tk.END)
        self.log_area.config(state="disabled")
        self.scan_progress['value'] = 0
        self.api_progress['value'] = 0
        self.log_message("--- Starting RimWorld Mod Database Update ---", "title")
        self.update_thread = threading.Thread(
            target=run_update_logic,
            args=(self.mods_dir, self.queue),
            daemon=True
        )
        self.update_thread.start()
        self.process_queue()

    def process_queue(self):
        try:
            while not self.queue.empty():
                msg_type, data = self.queue.get_nowait()
                if msg_type == "log":
                    message, tag = data
                    self.log_message(message, tag)
                elif msg_type == "error_log":
                    self.log_message(data, "error")
                elif msg_type == "scan_progress_config":
                    self.scan_progress.config(maximum=data)
                elif msg_type == "scan_progress_update":
                    self.scan_progress.step(data)
                elif msg_type == "api_progress_config":
                    self.api_progress.config(maximum=data)
                elif msg_type == "api_progress_update":
                    self.api_progress.step(data)
                elif msg_type == "done":
                    self.log_message("--- Update Process Finished ---", "title")
                    self.start_button.config(state="normal")
                    self.select_folder_button.config(state="normal")
                    tk.messagebox.showinfo("Success", "The mod database update process has completed.")
                    return
            self.root.after(100, self.process_queue)
        except queue.Empty:
            self.root.after(100, self.process_queue)
    
    def log_message(self, message, tag=None):
        self.log_area.config(state="normal")
        tag_config = {"error": "red", "title": "blue", "success": "green"}
        if tag and tag in tag_config:
            self.log_area.tag_configure(tag, foreground=tag_config[tag])
            self.log_area.insert(tk.END, message + "\n", tag)
        else:
            self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state="disabled")

def run_update_logic(mods_dir, q):
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(async_worker(mods_dir, q))
    except Exception as e:
        q.put(("error_log", f"A fatal error occurred in the worker thread: {e}"))
    finally:
        q.put(("done", None))

async def async_worker(mods_dir, q):
    db_data = load_json_db(DB_JSON_FILE, q)
    mods_db = db_data.setdefault("mods", {})

    q.put(("log", (f"Scanning mods directory: {mods_dir}...", None)))
    mods_to_fetch_from_api, versions_updated_count, mods_added_count = [], 0, 0
    
    mod_folders = [item for item in mods_dir.iterdir() if item.is_dir() and item.name.isdigit()]
    total_mods = len(mod_folders)
    q.put(("scan_progress_config", total_mods))

    scan_counter = 0
    for item in mod_folders:
        scan_counter += 1
        local_mod_info = extract_mod_info_from_xml(item / "About" / "About.xml", q)
        if local_mod_info:
            pkg_id, steam_id_str = local_mod_info["package_id"], local_mod_info["steam_id"]
            
            if pkg_id in mods_db and steam_id_str in mods_db[pkg_id]:
                # --- THIS IS THE NEW LOGIC BLOCK FOR EXISTING MODS ---
                db_entry = mods_db[pkg_id][steam_id_str]
                local_versions = set(local_mod_info["data"]["versions"])
                db_versions = set(db_entry.get("versions", []))

                if not local_versions: # Skip if local has no version info
                    continue

                if not db_versions: # If DB is empty, local versions win
                    db_entry["versions"] = sorted(list(local_versions), key=get_version_key)
                    versions_updated_count += 1
                    q.put(("log", (f"  - Update '{pkg_id}': Populating empty DB versions.", None)))
                    continue
                
                # Compare using the new version logic
                max_local_ver_key = get_version_key(max(local_versions, key=get_version_key))
                max_db_ver_key = get_version_key(max(db_versions, key=get_version_key))

                should_replace = False
                reason = ""
                if max_local_ver_key > max_db_ver_key:
                    should_replace = True
                    reason = "local has a newer max version"
                elif max_local_ver_key == max_db_ver_key and len(local_versions) < len(db_versions):
                    should_replace = True
                    reason = "local has fewer (more precise) versions"
                
                if should_replace and local_versions != db_versions:
                    db_entry["versions"] = sorted(list(local_versions), key=get_version_key)
                    versions_updated_count += 1
                    q.put(("log", (f"  - Update '{pkg_id}': Replacing DB versions because {reason}.", "success")))

            else: # Logic for new packageIds or new steamIds
                mods_added_count += 1
                if pkg_id not in mods_db:
                    mods_db[pkg_id] = {}
                mods_db[pkg_id][steam_id_str] = local_mod_info["data"]
                mods_to_fetch_from_api.append((steam_id_str, mods_db[pkg_id][steam_id_str]))

        if scan_counter % BATCH_SIZE == 0 or scan_counter == total_mods:
            q.put(("scan_progress_update", BATCH_SIZE if scan_counter % BATCH_SIZE == 0 else scan_counter % BATCH_SIZE))

    # --- API Fetching Block (No logic change needed here) ---
    if mods_to_fetch_from_api:
        q.put(("log", (f"\nFound {len(mods_to_fetch_from_api)} new mods. Fetching details from Steam API...", None)))
        total_api_calls = len(mods_to_fetch_from_api)
        q.put(("api_progress_config", total_api_calls))
        
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        successful_api_updates, failed_api_updates = 0, 0
        
        async with aiohttp.ClientSession() as session:
            tasks = [fetch_steam_details(session, sid, semaphore, q) for sid, _ in mods_to_fetch_from_api]
            ref_map = {sid: ref for sid, ref in mods_to_fetch_from_api}
            
            api_counter = 0
            for future in asyncio.as_completed(tasks):
                api_counter += 1
                steam_id, api_result = await future
                mod_ref = ref_map[steam_id] # Find the mod entry in our DB data
                if api_result:
                    # API is the source of truth, so we REPLACE local versions.
                    mod_ref["versions"] = filter_api_version_tags(api_result.get("tags", []), q)
                    mod_ref["published"] = True
                    successful_api_updates += 1
                else:
                    mod_ref["published"] = False # Keep local versions as fallback
                    failed_api_updates += 1
                
                if api_counter % BATCH_SIZE == 0 or api_counter == total_api_calls:
                    q.put(("api_progress_update", BATCH_SIZE if api_counter % BATCH_SIZE == 0 else api_counter % BATCH_SIZE))

        q.put(("log", ("\nAPI Fetch Summary:", None)))
        q.put(("log", (f"  - Successfully enriched: {successful_api_updates} mods", "success")))
        q.put(("log", (f"  - Failed to enrich:      {failed_api_updates} mods", "error" if failed_api_updates > 0 else None)))
    else:
        q.put(("log", ("\nNo new mods to check against the Steam API.", None)))

    save_json_db(DB_JSON_FILE, db_data, q)
    q.put(("log", ("\nFinal Summary:", None)))
    q.put(("log", (f"  - {versions_updated_count} existing mods had their version lists updated.", None)))
    q.put(("log", (f"  - {mods_added_count} new mod entries were added and/or enriched.", None)))

# --- Helper Functions (largely unchanged) ---
def load_json_db(filepath, q):
    if not filepath.exists():
        q.put(("log", (f"INFO: Database file '{filepath}' not found. Starting with a new structure.", None)))
        return {"mods": {}}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        q.put(("error_log", f"ERROR: Could not load JSON from '{filepath}': {e}"))
        return {"mods": {}}

def save_json_db(filepath, data, q):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        q.put(("log", (f"INFO: Successfully saved updated database to '{filepath}'", "success")))
    except Exception as e:
        q.put(("error_log", f"ERROR: Could not save data to '{filepath}': {e}"))

def extract_mod_info_from_xml(about_xml_path, q):
    try:
        if not about_xml_path.exists(): return None
        tree = ET.parse(about_xml_path)
        root = tree.getroot()
        package_id = (root.findtext('packageId') or "").strip().lower()
        if not package_id: return None
        versions = [li.text.strip() for li in root.findall('.//supportedVersions/li') if li.text]
        return {
            "package_id": package_id,
            "steam_id": about_xml_path.parent.parent.name,
            "data": { "name": (root.findtext('name') or "Unknown Name").strip(), "authors": (root.findtext('authors') or root.findtext('author') or "Unknown Author").strip(), "versions": versions }
        }
    except Exception: return None

def filter_api_version_tags(tags, q):
    version_tags = [tag for tag in tags if VERSION_REGEX.match(tag)]
    # Always sort the versions from the API
    return sorted(version_tags, key=get_version_key)

async def fetch_steam_details(session, steam_id, semaphore, q):
    async with semaphore:
        payload = {"itemcount": "1", "publishedfileids[0]": steam_id}
        try:
            async with session.post(API_URL, data=payload, timeout=45) as response:
                if response.status != 200: return steam_id, None
                data = await response.json()
                details_list = data.get("response", {}).get("publishedfiledetails", [])
                if not details_list or details_list[0].get("result") != 1: return steam_id, None
                details = details_list[0]
                raw_tags = details.get("tags", [])
                processed_tags = [t.get("tag") for t in raw_tags if isinstance(t, dict) and t.get("tag")]
                return steam_id, {"tags": processed_tags}
        except Exception: return steam_id, None

if __name__ == "__main__":
    app_root = tk.Tk()
    app = ModUpdaterApp(app_root)
    app_root.mainloop()