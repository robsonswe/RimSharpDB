import json
from pathlib import Path
import sys
import re
from typing import Dict, Any, Optional, List, Tuple, Union
import asyncio

import threading
import queue

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import simpledialog # For input pop-ups

# --- Configuration ---
ROOT_DIR = Path(__file__).resolve().parent.parent
DB_DIR = ROOT_DIR / "db"

RULES_JSON_FILE = DB_DIR / "rules.json"
DB_JSON_FILE = DB_DIR / "db.json"  # Source for mod names/authors

# --- Helper Functions ---
# (Using get_version_key from previous scripts for consistency, though not strictly for comparison here)
def get_version_key(version_str: str) -> Tuple[int, ...]:
    try: return tuple(map(int, version_str.split('.')))
    except (ValueError, AttributeError): return (0,)

# --- Global DB Data Cache for Mod Details ---
# Loads db.json once at startup and flattens it for easy lookup by SteamId or PackageId
_GLOBAL_DB_MOD_DETAILS: Dict[str, Dict[str, Any]] = {} # Maps SteamID -> {package_id, name, authors, versions, published}
_GLOBAL_DB_PACKAGEID_TO_STEAMID: Dict[str, List[str]] = {} # Maps packageId -> [SteamIDs]

def _load_and_flatten_db_json():
    global _GLOBAL_DB_MOD_DETAILS, _GLOBAL_DB_PACKAGEID_TO_STEAMID
    _GLOBAL_DB_MOD_DETAILS = {}
    _GLOBAL_DB_PACKAGEID_TO_STEAMID = {}

    if not DB_JSON_FILE.exists():
        messagebox.showwarning("DB File Missing", f"'{DB_JSON_FILE.name}' not found. Mod details will be limited.")
        return
    try:
        with open(DB_JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if "mods" in data and isinstance(data["mods"], dict):
                for package_id, steam_ids_dict in data["mods"].items():
                    if isinstance(steam_ids_dict, dict):
                        _GLOBAL_DB_PACKAGEID_TO_STEAMID.setdefault(package_id.lower(), [])
                        for steam_id, details in steam_ids_dict.items():
                            if steam_id.isdigit():
                                _GLOBAL_DB_MOD_DETAILS[steam_id] = {
                                    "package_id": package_id.lower(), # Store package_id normalized
                                    "name": details.get("name", "Unknown Name"),
                                    "versions": [v.strip() for v in details.get("versions", []) if isinstance(v, str)],
                                    "authors": [a.strip() for a in details.get("authors", "").split(',') if a.strip()],
                                    "published": details.get("published", False)
                                }
                                _GLOBAL_DB_PACKAGEID_TO_STEAMID[package_id.lower()].append(steam_id)
    except (json.JSONDecodeError, IOError) as e:
        messagebox.showerror("DB Load Error", f"Error loading '{DB_JSON_FILE.name}': {e}\nPlease check its format.")
_load_and_flatten_db_json() # Load DB on script start

def get_mod_details_from_db(package_id: str) -> Optional[Dict[str, Any]]:
    """Fetches mod details by package_id from the global DB cache."""
    # Find any SteamID associated with this packageId
    steam_ids = _GLOBAL_DB_PACKAGEID_TO_STEAMID.get(package_id.lower())
    if steam_ids:
        # Prioritize a published version if multiple SteamIDs exist for a packageId
        for steam_id in steam_ids:
            details = _GLOBAL_DB_MOD_DETAILS.get(steam_id)
            if details and details.get("published"):
                return details
        # If no published version found, return details of the first one
        return _GLOBAL_DB_MOD_DETAILS.get(steam_ids[0])
    return None

# --- Data Models (Mirroring C# structures) ---
class ModDependencyRule:
    def __init__(self, name: Union[str, List[str]] = "", comment: Union[str, List[str]] = ""):
        self.Name = [name] if isinstance(name, str) else name
        self.Comment = [comment] if isinstance(comment, str) else comment

    def to_dict(self):
        return {"name": self.Name, "comment": self.Comment}

class ModIncompatibilityRule:
    def __init__(self, hard_incompatibility: bool = False, comment: Union[str, List[str]] = "", name: Union[str, List[str]] = ""):
        self.HardIncompatibility = hard_incompatibility
        self.Comment = [comment] if isinstance(comment, str) else comment
        self.Name = [name] if isinstance(name, str) else name

    def to_dict(self):
        return {"hardIncompatibility": self.HardIncompatibility, "comment": self.Comment, "name": self.Name}

class LoadBottomRule:
    def __init__(self, value: bool = False, comment: Union[str, List[str]] = ""):
        self.Value = value
        self.Comment = [comment] if isinstance(comment, str) else comment

    def to_dict(self):
        return {"value": self.Value, "comment": self.Comment}

class ModRule:
    def __init__(self):
        self.LoadBefore: Dict[str, ModDependencyRule] = {}
        self.LoadAfter: Dict[str, ModDependencyRule] = {}
        self.LoadBottom: Optional[LoadBottomRule] = None
        self.Incompatibilities: Dict[str, ModIncompatibilityRule] = {}
        self.SupportedVersions: List[str] = []

    @staticmethod
    def from_dict(data: Dict[str, Any]):
        rule = ModRule()
        if "loadBefore" in data:
            rule.LoadBefore = {k: ModDependencyRule(name=v.get("name", []), comment=v.get("comment", [])) for k, v in data["loadBefore"].items()}
        if "loadAfter" in data:
            rule.LoadAfter = {k: ModDependencyRule(name=v.get("name", []), comment=v.get("comment", [])) for k, v in data["loadAfter"].items()}
        if "loadBottom" in data and data["loadBottom"] is not None:
            rule.LoadBottom = LoadBottomRule(value=data["loadBottom"].get("value", False), comment=data["loadBottom"].get("comment", []))
        if "incompatibilities" in data:
            rule.Incompatibilities = {k: ModIncompatibilityRule(hard_incompatibility=v.get("hardIncompatibility", False), comment=v.get("comment", []), name=v.get("name", [])) for k, v in data["incompatibilities"].items()}
        if "supportedVersions" in data and data["supportedVersions"] is not None:
            # Handle StringOrStringListConverter behavior
            versions = data["supportedVersions"]
            rule.SupportedVersions = [versions] if isinstance(versions, str) else versions
        return rule

    def to_dict(self):
        data = {}
        if self.LoadBefore: data["loadBefore"] = {k: v.to_dict() for k, v in self.LoadBefore.items()}
        if self.LoadAfter: data["loadAfter"] = {k: v.to_dict() for k, v in self.LoadAfter.items()}
        if self.LoadBottom: data["loadBottom"] = self.LoadBottom.to_dict()
        if self.Incompatibilities: data["incompatibilities"] = {k: v.to_dict() for k, v in self.Incompatibilities.items()}
        if self.SupportedVersions: data["supportedVersions"] = self.SupportedVersions # Python handles list -> JSON array
        return data

# --- Rules Repository (File Handling) ---
class ModRulesRepository:
    def __init__(self, filepath: Path):
        self.filepath = filepath

    def get_all_rules(self) -> Dict[str, ModRule]:
        if not self.filepath.exists():
            return {}
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
                rules_data = raw_data.get("rules", {})
                # Normalize keys to lowercase when loading, similar to C#
                return {k.lower(): ModRule.from_dict(v) for k, v in rules_data.items()}
        except (json.JSONDecodeError, IOError) as e:
            messagebox.showerror("Rules Load Error", f"Error loading '{self.filepath.name}': {e}\nPlease check its format or delete it to start fresh.")
            return {}

    def save_rules(self, rules: Dict[str, ModRule]):
        try:
            # Convert ModRule objects to dictionaries for JSON serialization
            serializable_rules = {k: v.to_dict() for k, v in rules.items()}
            # Add timestamp just like the C# example
            output_data = {"timestamp": int(datetime.now().timestamp()), "rules": serializable_rules}
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=4, ensure_ascii=False)
            messagebox.showinfo("Save Success", f"Rules saved to '{self.filepath.name}' successfully.")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save rules to '{self.filepath.name}': {e}")

# --- Async Worker for Background Operations ---
def run_async_worker(async_func, q: queue.Queue, *args):
    try:
        if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(async_func(q, *args))
    except Exception as e:
        q.put(("error_info", f"An unexpected error occurred: {e}"))

# --- Main Fetch Logic for Mod Rules ---
async def async_fetch_rule_worker(q: queue.Queue, package_id: str):
    repo = ModRulesRepository(RULES_JSON_FILE)
    all_rules = repo.get_all_rules()
    
    # Try to get details from db.json for display
    mod_details = get_mod_details_from_db(package_id)

    if not mod_details:
        q.put(("failure_info", f"Package ID '{package_id}' not found in '{DB_JSON_FILE.name}'. Please ensure it's installed and run 'db_updater.py' first."))
        return

    # Normalize packageId for lookup (similar to C# ModRulesService)
    normalized_package_id = package_id.lower()

    if normalized_package_id in all_rules:
        # Load existing rule
        rule_data = all_rules[normalized_package_id].to_dict() # Convert back to dict for sending
        q.put(("load_success", {"package_id": normalized_package_id, "rule": rule_data, "mod_details": mod_details, "is_new": False}))
    else:
        # Initialize new rule
        q.put(("load_success", {"package_id": normalized_package_id, "rule": ModRule().to_dict(), "mod_details": mod_details, "is_new": True}))

# --- Tkinter App ---
class RulesManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RimWorld Rules Manager")
        self.root.geometry("1000x700")

        self.repository = ModRulesRepository(RULES_JSON_FILE)
        self.current_package_id: Optional[str] = None
        self.current_mod_rule: Optional[ModRule] = None
        self.is_new_rule: bool = False
        self.queue = queue.Queue()

        self._setup_ui()
        self._reset_ui_state() # Initial UI state
        self.process_queue() # Start listening for worker messages

    def _setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10"); main_frame.pack(fill=tk.BOTH, expand=True)
        
        # --- Top Section: Package ID Input and Load/New ---
        top_frame = ttk.Frame(main_frame); top_frame.pack(fill=tk.X, pady=5)
        top_frame.columnconfigure(1, weight=1)
        ttk.Label(top_frame, text="Package ID:").grid(row=0, column=0, sticky="w", padx=(0,5))
        self.package_id_var = tk.StringVar()
        self.package_id_entry = ttk.Entry(top_frame, textvariable=self.package_id_var)
        self.package_id_entry.grid(row=0, column=1, sticky="ew")
        self.load_button = ttk.Button(top_frame, text="Load / New", command=self._load_or_new_rule)
        self.load_button.grid(row=0, column=2, padx=(5,0))

        # --- Mod Details Display (from db.json) ---
        details_frame = ttk.LabelFrame(main_frame, text="Mod Details (from db.json)", padding="10"); details_frame.pack(fill=tk.X, pady=5)
        details_frame.columnconfigure(1, weight=1); details_frame.columnconfigure(3, weight=1)
        self.mod_name_var = tk.StringVar(value="N/A"); self.mod_authors_var = tk.StringVar(value="N/A")
        self.mod_versions_var = tk.StringVar(value="N/A"); self.mod_published_var = tk.StringVar(value="N/A")

        ttk.Label(details_frame, text="Name:").grid(row=0, column=0, sticky="w"); ttk.Label(details_frame, textvariable=self.mod_name_var, wraplength=400).grid(row=0, column=1, sticky="w")
        ttk.Label(details_frame, text="Authors:").grid(row=1, column=0, sticky="w"); ttk.Label(details_frame, textvariable=self.mod_authors_var, wraplength=400).grid(row=1, column=1, sticky="w")
        ttk.Label(details_frame, text="Versions:").grid(row=0, column=2, sticky="w", padx=(10,0)); ttk.Label(details_frame, textvariable=self.mod_versions_var, wraplength=200).grid(row=0, column=3, sticky="w")
        ttk.Label(details_frame, text="Published:").grid(row=1, column=2, sticky="w", padx=(10,0)); ttk.Label(details_frame, textvariable=self.mod_published_var).grid(row=1, column=3, sticky="w")
        
        # --- Rule Editing Tabs ---
        notebook = ttk.Notebook(main_frame); notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # Supported Versions Tab
        sv_frame = ttk.Frame(notebook, padding="10"); notebook.add(sv_frame, text="Supported Versions")
        ttk.Label(sv_frame, text="Comma-separated versions (e.g., 1.3, 1.4, 1.5):").pack(anchor="w", pady=(0,5))
        self.supported_versions_text = tk.Text(sv_frame, height=3, wrap=tk.WORD)
        self.supported_versions_text.pack(fill=tk.BOTH, expand=True)

        # Load Bottom Tab
        lb_frame = ttk.Frame(notebook, padding="10"); notebook.add(lb_frame, text="Load Bottom")
        self.load_bottom_value_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(lb_frame, text="Load this mod at the bottom of the load order", variable=self.load_bottom_value_var).pack(anchor="w", pady=(0,5))
        ttk.Label(lb_frame, text="Comment (optional, comma-separated):").pack(anchor="w", pady=(5,0))
        self.load_bottom_comment_text = tk.Text(lb_frame, height=3, wrap=tk.WORD)
        self.load_bottom_comment_text.pack(fill=tk.BOTH, expand=True)

        # Load Before Tab
        lb_dep_frame = ttk.Frame(notebook, padding="10"); notebook.add(lb_dep_frame, text="Load Before")
        self._setup_dependency_list_ui(lb_dep_frame, "loadBefore")

        # Load After Tab
        la_dep_frame = ttk.Frame(notebook, padding="10"); notebook.add(la_dep_frame, text="Load After")
        self._setup_dependency_list_ui(la_dep_frame, "loadAfter")

        # Incompatibilities Tab
        inc_frame = ttk.Frame(notebook, padding="10"); notebook.add(inc_frame, text="Incompatibilities")
        self._setup_incompatibility_list_ui(inc_frame, "incompatibilities")

        # --- Bottom Section: Save/Delete/Reset ---
        bottom_buttons_frame = ttk.Frame(main_frame); bottom_buttons_frame.pack(fill=tk.X, pady=5)
        self.save_button = ttk.Button(bottom_buttons_frame, text="Save Changes", command=self._save_rules, state="disabled")
        self.save_button.pack(side=tk.LEFT, padx=(0, 10))
        self.delete_button = ttk.Button(bottom_buttons_frame, text="Delete Rule", command=self._delete_rule, state="disabled")
        self.delete_button.pack(side=tk.LEFT)
        self.reset_button = ttk.Button(bottom_buttons_frame, text="Reset UI", command=self._reset_ui_state)
        self.reset_button.pack(side=tk.RIGHT)

    def _setup_dependency_list_ui(self, parent_frame, list_type: str):
        # Treeview for dependencies
        tree = ttk.Treeview(parent_frame, columns=("Package ID", "Display Name", "Comment"), show="headings")
        tree.heading("Package ID", text="Package ID", anchor="w")
        tree.heading("Display Name", text="Display Name", anchor="w")
        tree.heading("Comment", text="Comment", anchor="w")
        tree.column("Package ID", width=150, stretch=tk.NO)
        tree.column("Display Name", width=200, stretch=tk.NO)
        tree.column("Comment", width=300, stretch=tk.YES)
        tree.pack(fill=tk.BOTH, expand=True)
        
        # Scrollbar for Treeview
        vsb = ttk.Scrollbar(parent_frame, orient="vertical", command=tree.yview)
        vsb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=vsb.set)

        # Store treeview reference
        setattr(self, f"{list_type}_tree", tree)

        # Buttons
        button_frame = ttk.Frame(parent_frame); button_frame.pack(fill=tk.X, pady=5)
        ttk.Button(button_frame, text="Add", command=lambda: self._add_dependency_rule(list_type)).pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(button_frame, text="Edit", command=lambda: self._edit_dependency_rule(list_type)).pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(button_frame, text="Remove", command=lambda: self._remove_dependency_rule(list_type)).pack(side=tk.LEFT)

    def _setup_incompatibility_list_ui(self, parent_frame, list_type: str):
        # Treeview for incompatibilities
        tree = ttk.Treeview(parent_frame, columns=("Package ID", "Display Name", "Hard Incomp.", "Comment"), show="headings")
        tree.heading("Package ID", text="Package ID", anchor="w")
        tree.heading("Display Name", text="Display Name", anchor="w")
        tree.heading("Hard Incomp.", text="Hard Incomp.", anchor="w")
        tree.heading("Comment", text="Comment", anchor="w")
        tree.column("Package ID", width=150, stretch=tk.NO)
        tree.column("Display Name", width=200, stretch=tk.NO)
        tree.column("Hard Incomp.", width=80, stretch=tk.NO)
        tree.column("Comment", width=300, stretch=tk.YES)
        tree.pack(fill=tk.BOTH, expand=True)

        # Scrollbar for Treeview
        vsb = ttk.Scrollbar(parent_frame, orient="vertical", command=tree.yview)
        vsb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=vsb.set)

        # Store treeview reference
        setattr(self, f"{list_type}_tree", tree)

        # Buttons
        button_frame = ttk.Frame(parent_frame); button_frame.pack(fill=tk.X, pady=5)
        ttk.Button(button_frame, text="Add", command=lambda: self._add_incompatibility_rule(list_type)).pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(button_frame, text="Edit", command=lambda: self._edit_incompatibility_rule(list_type)).pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(button_frame, text="Remove", command=lambda: self._remove_incompatibility_rule(list_type)).pack(side=tk.LEFT)

    def _load_or_new_rule(self):
        package_id = self.package_id_var.get().strip()
        if not package_id:
            messagebox.showwarning("Input Error", "Please enter a Package ID.")
            return

        self._reset_ui_state() # Reset before loading/creating
        self.package_id_entry.config(state='disabled') # Disable input while processing
        self.load_button.config(state='disabled')

        # Run fetch in a thread
        threading.Thread(target=run_async_worker, args=(async_fetch_rule_worker, self.queue, package_id), daemon=True).start()

    def process_queue(self):
        try:
            while not self.queue.empty():
                msg_type, data = self.queue.get_nowait()
                
                if msg_type == "load_success":
                    self.current_package_id = data["package_id"] # Ensure it's the normalized ID
                    self.current_mod_rule = ModRule.from_dict(data["rule"])
                    self.is_new_rule = data["is_new"]
                    self._populate_ui(data["mod_details"])
                    self._update_action_buttons()
                elif msg_type == "failure_info":
                    messagebox.showerror("Mod Not Found", data)
                    self._reset_ui_state() # Clear UI on failure
                elif msg_type == "error_info":
                    messagebox.showerror("Error", data)
                    self._reset_ui_state()
            self.root.after(100, self.process_queue)
        except queue.Empty:
            self.root.after(100, self.process_queue)
        finally:
            # Re-enable input fields after processing
            if self.package_id_entry.cget('state') == 'disabled':
                 self.package_id_entry.config(state='normal')
                 self.load_button.config(state='normal')


    def _populate_ui(self, mod_details: Dict[str, Any]):
        # Populate Mod Details section
        self.mod_name_var.set(mod_details.get("name", "N/A"))
        self.mod_authors_var.set(", ".join(mod_details.get("authors", ["N/A"])))
        self.mod_versions_var.set(", ".join(mod_details.get("versions", ["N/A"])))
        self.mod_published_var.set("Yes" if mod_details.get("published") else "No / Unavailable")

        # Populate Rule editing sections
        rule = self.current_mod_rule

        # Supported Versions
        self.supported_versions_text.delete("1.0", tk.END)
        self.supported_versions_text.insert(tk.END, ", ".join(rule.SupportedVersions))

        # Load Bottom
        if rule.LoadBottom:
            self.load_bottom_value_var.set(rule.LoadBottom.Value)
            self.load_bottom_comment_text.delete("1.0", tk.END)
            self.load_bottom_comment_text.insert(tk.END, ", ".join(rule.LoadBottom.Comment))
        else:
            self.load_bottom_value_var.set(False)
            self.load_bottom_comment_text.delete("1.0", tk.END)

        # Load Before, Load After, Incompatibilities
        self._populate_treeview(self.loadBefore_tree, rule.LoadBefore, is_incomp=False)
        self._populate_treeview(self.loadAfter_tree, rule.LoadAfter, is_incomp=False)
        self._populate_treeview(self.incompatibilities_tree, rule.Incompatibilities, is_incomp=True)

    def _populate_treeview(self, tree, rules_dict, is_incomp: bool):
        for item in tree.get_children(): tree.delete(item)
        for pkg_id, rule_obj in rules_dict.items():
            display_name = ", ".join(getattr(rule_obj, "Name", [])) if hasattr(rule_obj, "Name") else ""
            comment = ", ".join(getattr(rule_obj, "Comment", [])) if hasattr(rule_obj, "Comment") else ""

            if is_incomp:
                hard_incomp = "Yes" if getattr(rule_obj, "HardIncompatibility", False) else "No"
                tree.insert("", tk.END, values=(pkg_id, display_name, hard_incomp, comment))
            else:
                tree.insert("", tk.END, values=(pkg_id, display_name, comment))

    def _update_action_buttons(self):
        self.save_button.config(state="normal")
        self.delete_button.config(state="normal" if not self.is_new_rule else "disabled")

    def _reset_ui_state(self):
        self.package_id_var.set("")
        self.mod_name_var.set("N/A"); self.mod_authors_var.set("N/A")
        self.mod_versions_var.set("N/A"); self.mod_published_var.set("N/A")
        self.supported_versions_text.delete("1.0", tk.END)
        self.load_bottom_value_var.set(False)
        self.load_bottom_comment_text.delete("1.0", tk.END)
        self.loadBefore_tree.delete(*self.loadBefore_tree.get_children())
        self.loadAfter_tree.delete(*self.loadAfter_tree.get_children())
        self.incompatibilities_tree.delete(*self.incompatibilities_tree.get_children())
        self.current_package_id = None
        self.current_mod_rule = None
        self.is_new_rule = False
        self.save_button.config(state="disabled")
        self.delete_button.config(state="disabled")
        self.package_id_entry.config(state='normal')
        self.load_button.config(state='normal')

    def _save_rules(self):
        if not self.current_package_id or not self.current_mod_rule:
            messagebox.showwarning("Save Error", "No mod rule loaded to save.")
            return

        # Collect data from UI back into self.current_mod_rule
        self.current_mod_rule.SupportedVersions = [s.strip() for s in self.supported_versions_text.get("1.0", tk.END).strip().split(',') if s.strip()]

        if self.load_bottom_value_var.get():
            self.current_mod_rule.LoadBottom = LoadBottomRule(
                value=True,
                comment=[s.strip() for s in self.load_bottom_comment_text.get("1.0", tk.END).strip().split(',') if s.strip()]
            )
        else:
            self.current_mod_rule.LoadBottom = None

        # Rebuild dependency/incompatibility dicts from treeviews
        self.current_mod_rule.LoadBefore = self._get_rules_from_treeview(self.loadBefore_tree, is_incomp=False)
        self.current_mod_rule.LoadAfter = self._get_rules_from_treeview(self.loadAfter_tree, is_incomp=False)
        self.current_mod_rule.Incompatibilities = self._get_rules_from_treeview(self.incompatibilities_tree, is_incomp=True)

        all_rules = self.repository.get_all_rules() # Get latest from file
        all_rules[self.current_package_id] = self.current_mod_rule # Update/add current rule

        self.repository.save_rules(all_rules)
        self.is_new_rule = False # It's no longer new after saving
        self._update_action_buttons()

    def _delete_rule(self):
        if not self.current_package_id or self.is_new_rule:
            messagebox.showwarning("Delete Error", "No existing rule loaded to delete.")
            return
        
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete the rule for '{self.current_package_id}'?"):
            all_rules = self.repository.get_all_rules()
            if self.current_package_id in all_rules:
                del all_rules[self.current_package_id]
                self.repository.save_rules(all_rules)
                messagebox.showinfo("Delete Success", f"Rule for '{self.current_package_id}' deleted.")
                self._reset_ui_state()

    def _get_rules_from_treeview(self, tree, is_incomp: bool) -> Union[Dict[str, ModDependencyRule], Dict[str, ModIncompatibilityRule]]:
        rules = {}
        for item_id in tree.get_children():
            values = tree.item(item_id, "values")
            pkg_id = values[0].lower() # Normalize package ID
            display_name = values[1]
            comment = values[2 if not is_incomp else 3] # Comment is at index 2 for dep, 3 for incomp

            if is_incomp:
                hard_incomp = values[2] == "Yes"
                rules[pkg_id] = ModIncompatibilityRule(hard_incompatibility=hard_incomp, name=[display_name], comment=[comment])
            else:
                rules[pkg_id] = ModDependencyRule(name=[display_name], comment=[comment])
        return rules

    # --- Rule Management Methods for Lists ---
    def _add_dependency_rule(self, list_type: str):
        dialog = DependencyRuleEditorDialog(self.root, "Add Dependency Rule")
        result = dialog.show()
        if result:
            package_id = result["package_id"].lower()
            if not self._validate_dependency_conflict(package_id, list_type, is_edit=False): return
            
            tree = getattr(self, f"{list_type}_tree")
            tree.insert("", tk.END, values=(package_id, result["display_name"], result["comment"]))

    def _edit_dependency_rule(self, list_type: str):
        tree = getattr(self, f"{list_type}_tree")
        selected_item = tree.focus()
        if not selected_item:
            messagebox.showwarning("Edit Error", "Please select a rule to edit.")
            return
        
        current_values = tree.item(selected_item, "values")
        dialog = DependencyRuleEditorDialog(self.root, "Edit Dependency Rule", current_values[0], current_values[1], current_values[2])
        result = dialog.show()
        if result:
            new_package_id = result["package_id"].lower()
            if new_package_id != current_values[0].lower(): # Only validate if package ID changed
                if not self._validate_dependency_conflict(new_package_id, list_type, is_edit=True, old_package_id=current_values[0].lower()): return
            
            tree.item(selected_item, values=(new_package_id, result["display_name"], result["comment"]))

    def _remove_dependency_rule(self, list_type: str):
        tree = getattr(self, f"{list_type}_tree")
        selected_item = tree.focus()
        if selected_item: tree.delete(selected_item)

    def _add_incompatibility_rule(self, list_type: str):
        dialog = IncompatibilityRuleEditorDialog(self.root, "Add Incompatibility Rule")
        result = dialog.show()
        if result:
            package_id = result["package_id"].lower()
            if not self._validate_dependency_conflict(package_id, list_type, is_edit=False): return
            
            tree = getattr(self, f"{list_type}_tree")
            tree.insert("", tk.END, values=(package_id, result["display_name"], "Yes" if result["hard_incompatibility"] else "No", result["comment"]))

    def _edit_incompatibility_rule(self, list_type: str):
        tree = getattr(self, f"{list_type}_tree")
        selected_item = tree.focus()
        if not selected_item:
            messagebox.showwarning("Edit Error", "Please select a rule to edit.")
            return
        
        current_values = tree.item(selected_item, "values")
        dialog = IncompatibilityRuleEditorDialog(self.root, "Edit Incompatibility Rule", current_values[0], current_values[1], current_values[3], current_values[2]=="Yes") # Package ID, Name, Comment, Hard Incomp.
        result = dialog.show()
        if result:
            new_package_id = result["package_id"].lower()
            if new_package_id != current_values[0].lower(): # Only validate if package ID changed
                if not self._validate_dependency_conflict(new_package_id, list_type, is_edit=True, old_package_id=current_values[0].lower()): return
            
            tree.item(selected_item, values=(new_package_id, result["display_name"], "Yes" if result["hard_incompatibility"] else "No", result["comment"]))

    def _remove_incompatibility_rule(self, list_type: str):
        tree = getattr(self, f"{list_type}_tree")
        selected_item = tree.focus()
        if selected_item: tree.delete(selected_item)

    def _validate_dependency_conflict(self, package_id: str, current_list_type: str, is_edit: bool, old_package_id: Optional[str] = None) -> bool:
        """
        Validates that a package ID doesn't exist in other dependency/incompatibility lists
        for the current parent mod rule, and is not a duplicate within its own list.
        """
        mod_details = get_mod_details_from_db(package_id)
        display_name = mod_details.get("name", package_id) if mod_details else package_id

        # Check for existence in current list first (duplicate within same list)
        current_tree = getattr(self, f"{current_list_type}_tree")
        for item_id in current_tree.get_children():
            existing_pkg_id = current_tree.item(item_id, "values")[0].lower()
            if existing_pkg_id == package_id and not (is_edit and package_id == old_package_id):
                messagebox.showwarning("Duplicate Entry", f"Package ID '{display_name}' already exists in the current list.")
                return False

        # Check other lists
        all_list_types = ["loadBefore", "loadAfter", "incompatibilities"]
        for list_type in all_list_types:
            if list_type == current_list_type: continue # Skip current list
            
            other_tree = getattr(self, f"{list_type}_tree")
            for item_id in other_tree.get_children():
                existing_pkg_id = other_tree.item(item_id, "values")[0].lower()
                if existing_pkg_id == package_id:
                    messagebox.showwarning("Rule Conflict", f"Package ID '{display_name}' already exists in the '{list_type}' list. A package ID cannot be in multiple rule lists for this mod.")
                    return False
        
        return True

# --- Pop-up Dialogs for Dependency/Incompatibility Rules ---
class DependencyRuleEditorDialog(simpledialog.Dialog):
    def __init__(self, parent, title: str, package_id="", display_name="", comment=""):
        self.package_id = package_id
        self.display_name = display_name
        self.comment = comment
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text="Package ID:").grid(row=0, column=0, sticky="w", pady=5)
        self.package_id_entry = ttk.Entry(master)
        self.package_id_entry.grid(row=0, column=1, sticky="ew", pady=5, padx=5)
        self.package_id_entry.insert(0, self.package_id)
        self.package_id_entry.bind("<KeyRelease>", self._update_display_name_from_db) # Bind for auto-fill

        ttk.Label(master, text="Display Name:").grid(row=1, column=0, sticky="w", pady=5)
        self.display_name_entry = ttk.Entry(master)
        self.display_name_entry.grid(row=1, column=1, sticky="ew", pady=5, padx=5)
        self.display_name_entry.insert(0, self.display_name)
        
        ttk.Label(master, text="Comment (optional):").grid(row=2, column=0, sticky="w", pady=5)
        self.comment_entry = ttk.Entry(master)
        self.comment_entry.grid(row=2, column=1, sticky="ew", pady=5, padx=5)
        self.comment_entry.insert(0, self.comment)
        
        return self.package_id_entry # Initial focus

    def _update_display_name_from_db(self, event=None):
        """Attempts to auto-fill Display Name based on Package ID from db.json."""
        pkg_id = self.package_id_entry.get().strip()
        if pkg_id:
            mod_details = get_mod_details_from_db(pkg_id.lower())
            if mod_details:
                # Only autofill if the display name field is empty or matches the package_id
                # This prevents overwriting user's manually entered display name
                current_display_name = self.display_name_entry.get().strip()
                if not current_display_name or current_display_name.lower() == pkg_id.lower():
                    self.display_name_entry.delete(0, tk.END)
                    self.display_name_entry.insert(0, mod_details["name"])
            else:
                # If mod details not found, and display name was autofilled, clear it
                current_display_name = self.display_name_entry.get().strip()
                mod_details_by_current_name = get_mod_details_from_db(current_display_name.lower()) # Check if current text is a valid pkg_id
                if mod_details_by_current_name and mod_details_by_current_name.get("name") == current_display_name:
                    self.display_name_entry.delete(0, tk.END) # Clear if it was an autofill for a now-invalid ID

    def apply(self):
        self.package_id = self.package_id_entry.get().strip()
        self.display_name = self.display_name_entry.get().strip()
        self.comment = self.comment_entry.get().strip()
        
        if not self.package_id:
            messagebox.showwarning("Validation", "Package ID cannot be empty.")
            self.result = None # Prevent dialog from closing
            return
        self.result = {"package_id": self.package_id, "display_name": self.display_name, "comment": self.comment}

    def show(self):
        self.result = None
        self.parent.wait_window(self)
        return self.result

class IncompatibilityRuleEditorDialog(simpledialog.Dialog): # DO NOT inherit from DependencyRuleEditorDialog.body() for layout
    def __init__(self, parent, title: str, package_id="", display_name="", comment="", hard_incompatibility=False):
        self.package_id = package_id
        self.display_name = display_name
        self.comment = comment
        self.hard_incompatibility = hard_incompatibility
        super().__init__(parent, title)

    def body(self, master):
        # Explicitly lay out all widgets for precise control
        row_counter = 0
        
        ttk.Label(master, text="Package ID:").grid(row=row_counter, column=0, sticky="w", pady=5)
        self.package_id_entry = ttk.Entry(master)
        self.package_id_entry.grid(row=row_counter, column=1, sticky="ew", pady=5, padx=5)
        self.package_id_entry.insert(0, self.package_id)
        self.package_id_entry.bind("<KeyRelease>", self._update_display_name_from_db) # Bind for auto-fill
        row_counter += 1

        ttk.Label(master, text="Display Name:").grid(row=row_counter, column=0, sticky="w", pady=5)
        self.display_name_entry = ttk.Entry(master)
        self.display_name_entry.grid(row=row_counter, column=1, sticky="ew", pady=5, padx=5)
        self.display_name_entry.insert(0, self.display_name)
        row_counter += 1

        # Hard Incompatibility checkbox
        self.hard_incompatibility_var = tk.BooleanVar(value=self.hard_incompatibility)
        ttk.Checkbutton(master, text="Hard Incompatibility", variable=self.hard_incompatibility_var).grid(row=row_counter, columnspan=2, sticky="w", pady=5)
        row_counter += 1
        
        ttk.Label(master, text="Comment (optional):").grid(row=row_counter, column=0, sticky="w", pady=5)
        self.comment_entry = ttk.Entry(master)
        self.comment_entry.grid(row=row_counter, column=1, sticky="ew", pady=5, padx=5)
        self.comment_entry.insert(0, self.comment)
        row_counter += 1
        
        return self.package_id_entry # Initial focus

    def _update_display_name_from_db(self, event=None):
        """Attempts to auto-fill Display Name based on Package ID from db.json."""
        pkg_id = self.package_id_entry.get().strip()
        if pkg_id:
            mod_details = get_mod_details_from_db(pkg_id.lower())
            if mod_details:
                current_display_name = self.display_name_entry.get().strip()
                if not current_display_name or current_display_name.lower() == pkg_id.lower():
                    self.display_name_entry.delete(0, tk.END)
                    self.display_name_entry.insert(0, mod_details["name"])
            else:
                current_display_name = self.display_name_entry.get().strip()
                mod_details_by_current_name = get_mod_details_from_db(current_display_name.lower())
                if mod_details_by_current_name and mod_details_by_current_name.get("name") == current_display_name:
                    self.display_name_entry.delete(0, tk.END)

    def apply(self):
        self.package_id = self.package_id_entry.get().strip()
        self.display_name = self.display_name_entry.get().strip()
        self.comment = self.comment_entry.get().strip()
        
        if not self.package_id:
            messagebox.showwarning("Validation", "Package ID cannot be empty.")
            self.result = None # Prevent dialog from closing
            return
        self.result = {"package_id": self.package_id, "display_name": self.display_name, "comment": self.comment, "hard_incompatibility": self.hard_incompatibility_var.get()}

    def show(self):
        self.result = None
        self.parent.wait_window(self)
        return self.result

# --- Main execution ---
if __name__ == "__main__":
    from datetime import datetime # Import here for timestamp
    app_root = tk.Tk()
    app = RulesManagerApp(app_root)
    app_root.mainloop()