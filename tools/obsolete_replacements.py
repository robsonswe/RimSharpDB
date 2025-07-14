import json
from pathlib import Path
from typing import Tuple, List, Dict, Any

def get_version_key(version_str: str) -> Tuple[int, ...]:
    """
    Converts a version string like '1.5.2' into a tuple (1, 5, 2)
    for accurate numerical comparison.
    """
    if not version_str or not isinstance(version_str, str):
        return (0,)
    try:
        # Clean the string to only contain digits and dots for safety
        cleaned_str = ''.join(filter(lambda char: char.isdigit() or char == '.', version_str))
        if not cleaned_str:
            return (0,)
        return tuple(map(int, cleaned_str.split('.')))
    except (ValueError, AttributeError):
        # Return a low-value tuple for un-parseable strings
        return (0,)

def get_max_version_key_from_list(versions: List[str]) -> Tuple[int, ...]:
    """
    Safely finds the highest version key from a list of version strings.
    Returns (0,) if the list is empty.
    """
    if not versions:
        return (0,)
    return max((get_version_key(v) for v in versions), default=(0,))

def create_steam_id_lookup(db_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Flattens the db.json structure into a simple dictionary keyed by Steam Workshop ID
    for fast and easy lookups.
    """
    lookup_table = {}
    mods_database = db_data.get("mods", {})
    for package_id, steam_entries in mods_database.items():
        for steam_id, mod_details in steam_entries.items():
            lookup_table[steam_id] = mod_details
    return lookup_table

def maintain_replacements_file(replacements_path: Path, db_path: Path):
    """
    Reads a replacements.json file, compares mod versions against a db.json,
    and filters out obsolete entries.
    The updated data then overwrites the original replacements.json file.

    An entry is considered obsolete if the original mod's version is
    strictly higher than the replacement's version.
    """
    print("--- Starting Maintenance Script for replacements.json ---")

    # 1. Load source JSON files
    try:
        with open(replacements_path, 'r', encoding='utf-8') as f:
            replacements_data = json.load(f)
        with open(db_path, 'r', encoding='utf-8') as f:
            db_data = json.load(f)
    except FileNotFoundError as e:
        print(f"Error: Could not find a required file. {e}")
        return
    except json.JSONDecodeError as e:
        print(f"Error: Could not parse a JSON file. {e}")
        return

    # 2. Create the fast lookup table from db.json
    steam_id_lookup = create_steam_id_lookup(db_data)
    print(f"Created a lookup table with {len(steam_id_lookup)} entries from {db_path.name}.")

    # 3. Iterate through replacements and decide which to keep or remove
    original_mod_entries = replacements_data.get("mods", {})
    kept_mod_entries = {}
    obsolete_mods_info = []

    print("\n--- Analyzing replacement entries ---")
    for original_steam_id, replacement_info in original_mod_entries.items():
        replacement_steam_id = replacement_info.get("ReplacementSteamId")
        original_mod_name = replacement_info.get('ModName', 'N/A')

        # --- Data Validation ---
        if not replacement_steam_id:
            print(f"  [WARN] Keeping {original_steam_id} ('{original_mod_name}') due to missing 'ReplacementSteamId'.")
            kept_mod_entries[original_steam_id] = replacement_info
            continue

        original_mod_db_entry = steam_id_lookup.get(original_steam_id)
        replacement_mod_db_entry = steam_id_lookup.get(replacement_steam_id)

        if not original_mod_db_entry:
            print(f"  [WARN] Keeping {original_steam_id} ('{original_mod_name}') as it was not found in the database.")
            kept_mod_entries[original_steam_id] = replacement_info
            continue
        
        if not replacement_mod_db_entry:
            replacement_name = replacement_info.get('ReplacementName', 'N/A')
            print(f"  [WARN] Keeping {original_steam_id} as its replacement {replacement_steam_id} ('{replacement_name}') was not found in database.")
            kept_mod_entries[original_steam_id] = replacement_info
            continue

        # --- Core Comparison Logic ---
        original_versions = original_mod_db_entry.get("versions", [])
        replacement_versions = replacement_mod_db_entry.get("versions", [])
        
        original_max_version = get_max_version_key_from_list(original_versions)
        replacement_max_version = get_max_version_key_from_list(replacement_versions)

        # The rule: An entry is obsolete if the original is strictly newer than the replacement.
        if original_max_version > replacement_max_version:
            original_version_str = ".".join(map(str, original_max_version))
            replacement_version_str = ".".join(map(str, replacement_max_version))
            obsolete_mods_info.append({
                "id": original_steam_id,
                "name": original_mod_name,
                "reason": f"Original version ({original_version_str}) > Replacement version ({replacement_version_str})."
            })
        else:
            kept_mod_entries[original_steam_id] = replacement_info

    # 4. Save the cleaned data to the original file
    new_replacements_data = {"mods": kept_mod_entries}
    
    try:
        with open(replacements_path, 'w', encoding='utf-8') as f:
            json.dump(new_replacements_data, f, indent=2, ensure_ascii=False) # Using indent 2 for smaller file size
    except Exception as e:
        print(f"\nFATAL ERROR: Could not save the updated replacements file to {replacements_path}. {e}")
        return

    # 5. Final Report
    print(f"\n--- Maintenance Complete ---")
    print(f"Total entries analyzed: {len(original_mod_entries)}")
    print(f"Entries removed as obsolete: {len(obsolete_mods_info)}")
    print(f"Entries kept: {len(kept_mod_entries)}")
    print(f"\nYour original file '{replacements_path.name}' has been updated.")

    if obsolete_mods_info:
        print("\n--- Details of Removed Obsolete Entries ---")
        for info in obsolete_mods_info:
            print(f"  - ID: {info['id']} ({info['name']})")
            print(f"    Reason: {info['reason']}")
        print("------------------------------------------")


# --- Main Execution Block ---
if __name__ == "__main__":
    # Go from /tools/ to /db/
    project_root = Path(__file__).resolve().parent.parent
    db_dir = project_root / "db"

    replacements_file = db_dir / "replacements.json"
    db_file = db_dir / "db.json"

    if not replacements_file.exists() or not db_file.exists():
        print("Error: Make sure 'replacements.json' and 'db.json' are located in the 'db' directory relative to the project root.")
    else:
        maintain_replacements_file(replacements_file, db_file)