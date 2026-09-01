#!/usr/bin/env python3
"""
Star Wars: Zero Company - Operator Share Tool

View, export and import custom Operators from the game's character databank
save file, so you can share them with friends.

No dependencies - just Python 3.8+ (tkinter for the GUI, included with
standard Python on Windows).

Usage:
    python zc_operators.py                 GUI
    python zc_operators.py list            list operators in the save
    python zc_operators.py export <name|#> [-o file.zcop]
    python zc_operators.py import <file.zcop>
    python zc_operators.py delete <name|#>

The save file is located automatically at:
    %LOCALAPPDATA%\\SWZeroCompany\\Saved\\SaveGames\\CharacterDatabank_Default_Custom_Characters.sav
Pass --save <path> to use a different file.

A timestamped backup of the save is written to a "Backups" folder next to
this script before any modification. Close the game before importing or
deleting; the game rewrites this file on save.
"""

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import struct
import sys
import uuid

FORMAT_ID = "zc-operator-v1"
SAVE_NAME = "CharacterDatabank_Default_Custom_Characters.sav"
EXPORT_EXT = ".zcop"


def default_save_path():
    base = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(base, "SWZeroCompany", "Saved", "SaveGames", SAVE_NAME)


# ---------------------------------------------------------------------------
# Minimal GVAS (UE 5.4+ property tag format) reader - just enough to locate
# the character pool map entries and save-data array items by byte range.
# ---------------------------------------------------------------------------

class Reader:
    def __init__(self, buf, off=0):
        self.b = buf
        self.o = off

    def u8(self):
        v = self.b[self.o]
        self.o += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.b, self.o)[0]
        self.o += 2
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.o)[0]
        self.o += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.o)[0]
        self.o += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.b, self.o)[0]
        self.o += 8
        return v

    def read(self, n):
        v = self.b[self.o:self.o + n]
        self.o += n
        return v

    def fstr(self):
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:
            return self.read(-n * 2).decode("utf-16-le")[:-1]
        return self.read(n).decode("utf-8", "replace")[:-1]


def read_typename(r):
    """FPropertyTypeName: name + recursive parameter list."""
    name = r.fstr()
    count = r.i32()
    kids = [read_typename(r) for _ in range(count)]
    return (name, kids)


def read_tag(r):
    """Read one property tag. Returns None at list end ('None' terminator).

    Tag layout (UE >= 5.4 / PROPERTY_TAG_COMPLETE_TYPE_NAME):
        FString name | typename tree | int32 size | uint8 flags | payload
    Bool values live in the flags byte (0x10), payload size 0.
    """
    name = r.fstr()
    if name == "None":
        return None
    tn = read_typename(r)
    size = r.i32()
    flags = r.u8()
    payload_start = r.o
    return {
        "name": name,
        "typename": tn,
        "type": tn[0],
        "size": size,
        "flags": flags,
        "payload": payload_start,
    }


def skip_prop_list(r):
    """Skip a terminated property list (ends with 'None'). All payloads are
    skipped via their declared size, so no per-type knowledge is needed."""
    while True:
        tag = read_tag(r)
        if tag is None:
            return
        r.o = tag["payload"] + tag["size"]


class SaveFile:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self._parse()

    def _parse(self):
        r = Reader(self.data)
        if r.read(4) != b"GVAS":
            raise ValueError("Not a GVAS save file: %s" % self.path)
        sg_version = r.u32()
        if sg_version >= 3:
            r.u32()  # UE4 package version
            r.u32()  # UE5 package version
        else:
            raise ValueError("Unsupported save game version %d" % sg_version)
        r.u16(); r.u16(); r.u16(); r.u32()  # engine version
        r.fstr()  # branch
        r.u32()  # custom version format
        n = r.u32()
        r.o += n * 20  # custom versions (guid + int32)
        self.save_class = r.fstr()
        r.u8()  # pad byte after class name in this format
        self.body_start = r.o

        self.map = None   # Characters map info
        self.arr = None   # CharacterSaveDataArray info
        self.pooldata_size_off = None

        while True:
            tag = read_tag(r)
            if tag is None:
                break
            if tag["name"] == "PoolData" and tag["type"] == "StructProperty":
                # size field sits 5 bytes before payload (int32 size + u8 flags)
                self.pooldata_size_off = tag["payload"] - 5
                self._parse_pooldata(r, tag)
            elif (tag["name"] == "CharacterSaveDataArray"
                  and tag["type"] == "ArrayProperty"):
                self._parse_array(r, tag)
            r.o = tag["payload"] + tag["size"]
        self.body_end = r.o

        if self.map is None or self.arr is None:
            raise ValueError("Character pool structures not found in save")
        self._match_characters()

    def _parse_pooldata(self, r, pool_tag):
        end = pool_tag["payload"] + pool_tag["size"]
        rr = Reader(self.data, pool_tag["payload"])
        while rr.o < end:
            tag = read_tag(rr)
            if tag is None:
                break
            if tag["name"] == "Characters" and tag["type"] == "MapProperty":
                info = {
                    "size_off": tag["payload"] - 5,
                    "size": tag["size"],
                    "payload": tag["payload"],
                    "count_off": tag["payload"] + 4,  # after num-removed
                    "entries": [],
                }
                mr = Reader(self.data, tag["payload"])
                mr.u32()  # keys-to-remove count
                count = mr.u32()
                for _ in range(count):
                    start = mr.o
                    key = bytes(mr.read(16))
                    skip_prop_list(mr)
                    info["entries"].append(
                        {"guid": key, "start": start, "end": mr.o})
                info["payload_end"] = tag["payload"] + tag["size"]
                self.map = info
            rr.o = tag["payload"] + tag["size"]

    def _parse_array(self, r, tag):
        info = {
            "size_off": tag["payload"] - 5,
            "size": tag["size"],
            "payload": tag["payload"],
            "count_off": tag["payload"],
            "items": [],
        }
        ar = Reader(self.data, tag["payload"])
        count = ar.u32()
        for _ in range(count):
            start = ar.o
            skip_prop_list(ar)
            info["items"].append({"start": start, "end": ar.o})
        info["payload_end"] = tag["payload"] + tag["size"]
        self.arr = info

    # -- character assembly --------------------------------------------------

    def _item_guid(self, item):
        rr = Reader(self.data, item["start"])
        while True:
            tag = read_tag(rr)
            if tag is None:
                return None
            if tag["name"] == "PoolCharacterID" and tag["size"] == 16:
                return bytes(self.data[tag["payload"]:tag["payload"] + 16])
            rr.o = tag["payload"] + tag["size"]

    def _item_datetime(self, item, prop="CharacterCreationTime"):
        rr = Reader(self.data, item["start"])
        while True:
            tag = read_tag(rr)
            if tag is None:
                return None
            if tag["name"] == prop and tag["size"] == 8:
                ticks = struct.unpack_from(
                    "<Q", self.data, tag["payload"])[0]
                try:
                    return (datetime.datetime(1, 1, 1)
                            + datetime.timedelta(microseconds=ticks / 10))
                except OverflowError:
                    return None
            rr.o = tag["payload"] + tag["size"]

    @staticmethod
    def _extract_name(blob, prop):
        """Pull FirstName/LastName out of the serialized archive bytes."""
        needle = prop.encode() + b"\x00\x0d\x00\x00\x00TextProperty\x00"
        i = blob.find(needle)
        if i < 0:
            return None
        rr = Reader(blob, i + len(needle))
        rr.i32()          # typename parameter count (0)
        rr.i32()          # payload size
        rr.u8()           # flags
        try:
            text = rr.fstr()
        except Exception:
            return None
        m = re.match(r'^(?:INVTEXT|NSLOCTEXT|LOCTEXT)\("?(.*?)"?\)$', text)
        if m:
            text = m.group(1).rsplit('", "')[-1].strip('"')
        return text

    def _match_characters(self):
        self.characters = []
        entries_by_guid = {e["guid"]: e for e in self.map["entries"]}
        for item in self.arr["items"]:
            guid = self._item_guid(item)
            blob = self.data[item["start"]:item["end"]]
            first = self._extract_name(blob, "FirstName") or ""
            last = self._extract_name(blob, "LastName") or ""
            self.characters.append({
                "guid": guid,
                "first": first,
                "last": last,
                "created": self._item_datetime(item),
                "updated": self._item_datetime(item, "LastUpdated"),
                "map_entry": entries_by_guid.get(guid),
                "arr_item": item,
            })

    # -- operations ----------------------------------------------------------

    def find(self, ident):
        """Find a character by 1-based index or (partial) name."""
        if ident.isdigit():
            i = int(ident) - 1
            if 0 <= i < len(self.characters):
                return self.characters[i]
            raise ValueError("No operator #%s (have %d)"
                             % (ident, len(self.characters)))
        idl = ident.lower()
        matches = [c for c in self.characters
                   if idl in ("%s %s" % (c["first"], c["last"])).lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError("No operator matching %r" % ident)
        raise ValueError("Ambiguous name %r matches %d operators"
                         % (ident, len(matches)))

    def export_char(self, char):
        if char["map_entry"] is None:
            raise ValueError("Operator has no pool map entry; save may be "
                             "from an unsupported game version")
        me = char["map_entry"]
        it = char["arr_item"]
        map_value = self.data[me["start"] + 16:me["end"]]
        arr_item = self.data[it["start"]:it["end"]]
        return {
            "format": FORMAT_ID,
            "game": "Star Wars: Zero Company",
            "save_class": self.save_class,
            "exported_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds"),
            "first_name": char["first"],
            "last_name": char["last"],
            "guid": char["guid"].hex(),
            "map_entry_value_b64": base64.b64encode(map_value).decode(),
            "array_item_b64": base64.b64encode(arr_item).decode(),
        }

    def import_char(self, package):
        if package.get("format") != FORMAT_ID:
            raise ValueError("Not a recognized operator export file")
        old_guid = bytes.fromhex(package["guid"])
        map_value = base64.b64decode(package["map_entry_value_b64"])
        arr_item = base64.b64decode(package["array_item_b64"])

        # Fresh GUID so an import never collides with an existing operator
        # (including re-importing next to the original).
        new_guid = uuid.uuid4().bytes
        map_value = map_value.replace(old_guid, new_guid)
        arr_item = arr_item.replace(old_guid, new_guid)

        data = self.data
        map_ins = self.map["payload_end"]
        arr_ins = self.arr["payload_end"]
        assert map_ins < arr_ins

        new_entry = new_guid + map_value
        out = bytearray()
        out += data[:map_ins]
        out += new_entry
        out += data[map_ins:arr_ins]
        out += arr_item
        out += data[arr_ins:]

        d1 = len(new_entry)
        self._patch_i32(out, self.pooldata_size_off, d1)
        self._patch_i32(out, self.map["size_off"], d1)
        self._patch_i32(out, self.map["count_off"], 1)
        self._patch_i32(out, self.arr["size_off"] + d1, len(arr_item))
        self._patch_i32(out, self.arr["count_off"] + d1, 1)
        return bytes(out)

    def delete_char(self, char):
        me = char["map_entry"]
        it = char["arr_item"]
        data = self.data
        out = bytearray()
        out += data[:me["start"]]
        out += data[me["end"]:it["start"]]
        out += data[it["end"]:]

        d1 = -(me["end"] - me["start"])
        d2 = -(it["end"] - it["start"])
        self._patch_i32(out, self.pooldata_size_off, d1)
        self._patch_i32(out, self.map["size_off"], d1)
        self._patch_i32(out, self.map["count_off"], -1)
        self._patch_i32(out, self.arr["size_off"] + d1, d2)
        self._patch_i32(out, self.arr["count_off"] + d1, -1)
        return bytes(out)

    @staticmethod
    def _patch_i32(buf, off, delta):
        v = struct.unpack_from("<i", buf, off)[0] + delta
        struct.pack_into("<i", buf, off, v)

    def write(self, new_data):
        backup_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "Backups")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(
            backup_dir, "%s.%s.bak" % (os.path.basename(self.path), stamp))
        shutil.copy2(self.path, backup)
        # sanity-check the new bytes parse cleanly before touching the save
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(new_data)
        try:
            SaveFile(tmp)
        except Exception:
            os.remove(tmp)
            raise
        os.replace(tmp, self.path)
        return backup


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def op_label(c, i=None):
    name = ("%s %s" % (c["first"], c["last"])).strip() or "(unnamed)"
    created = c["created"].strftime("%Y-%m-%d %H:%M") if c["created"] else "?"
    prefix = "%2d. " % i if i is not None else ""
    return "%s%-30s  created %s" % (prefix, name, created)


def cli_list(save):
    if not save.characters:
        print("No custom operators in this save.")
        return
    print("Custom operators in %s:" % save.path)
    for i, c in enumerate(save.characters, 1):
        print("  " + op_label(c, i))


def safe_filename(s):
    return re.sub(r"[^A-Za-z0-9_\- ]", "", s).strip().replace(" ", "_") \
        or "operator"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="View, export and import Star Wars: Zero Company "
                    "custom Operators.")
    ap.add_argument("--save", default=default_save_path(),
                    help="path to the character databank .sav")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list operators")
    p = sub.add_parser("export", help="export an operator to a .zcop file")
    p.add_argument("who", help="operator number (from list) or name")
    p.add_argument("-o", "--out", help="output file")
    p = sub.add_parser("import", help="import an operator from a .zcop file")
    p.add_argument("file")
    p = sub.add_parser("delete", help="remove an operator from the save")
    p.add_argument("who")
    args = ap.parse_args(argv)

    if args.cmd is None:
        return run_gui(args.save)

    if not os.path.exists(args.save):
        print("Save file not found: %s" % args.save, file=sys.stderr)
        return 1
    save = SaveFile(args.save)

    if args.cmd == "list":
        cli_list(save)
    elif args.cmd == "export":
        c = save.find(args.who)
        pkg = save.export_char(c)
        out = args.out or safe_filename(
            "%s %s" % (c["first"], c["last"])) + EXPORT_EXT
        with open(out, "w", encoding="utf-8") as f:
            json.dump(pkg, f, indent=1)
        print("Exported %s %s -> %s" % (c["first"], c["last"], out))
    elif args.cmd == "import":
        with open(args.file, "r", encoding="utf-8") as f:
            pkg = json.load(f)
        new_data = save.import_char(pkg)
        backup = save.write(new_data)
        print("Imported %s %s into the save."
              % (pkg.get("first_name", "?"), pkg.get("last_name", "")))
        print("Backup written to %s" % backup)
        print("Note: close/restart the game to see the new operator.")
    elif args.cmd == "delete":
        c = save.find(args.who)
        new_data = save.delete_char(c)
        backup = save.write(new_data)
        print("Deleted %s %s. Backup: %s" % (c["first"], c["last"], backup))
    return 0


# ---------------------------------------------------------------------------
# GUI (tkinter)
# ---------------------------------------------------------------------------

def run_gui(save_path):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Zero Company Operator Share")
    root.geometry("560x420")

    state = {"save": None}

    top = ttk.Frame(root, padding=8)
    top.pack(fill="both", expand=True)

    path_var = tk.StringVar(value=save_path)
    ttk.Label(top, text="Save file:").pack(anchor="w")
    prow = ttk.Frame(top)
    prow.pack(fill="x")
    ttk.Entry(prow, textvariable=path_var).pack(
        side="left", fill="x", expand=True)

    def browse():
        p = filedialog.askopenfilename(
            title="Select character databank save",
            filetypes=[("UE save", "*.sav"), ("All files", "*.*")])
        if p:
            path_var.set(p)
            refresh()

    ttk.Button(prow, text="...", width=3, command=browse).pack(
        side="left", padx=(4, 0))

    ttk.Label(top, text="Custom operators:").pack(anchor="w", pady=(8, 0))
    lb = tk.Listbox(top, font=("Consolas", 10))
    lb.pack(fill="both", expand=True, pady=4)

    status = tk.StringVar()
    ttk.Label(top, textvariable=status, foreground="gray").pack(anchor="w")

    def refresh():
        lb.delete(0, "end")
        p = path_var.get()
        if not os.path.exists(p):
            status.set("Save file not found.")
            state["save"] = None
            return
        try:
            state["save"] = SaveFile(p)
        except Exception as e:
            state["save"] = None
            status.set("Could not read save: %s" % e)
            return
        for i, c in enumerate(state["save"].characters, 1):
            lb.insert("end", op_label(c, i))
        n = len(state["save"].characters)
        status.set("%d operator%s loaded." % (n, "" if n == 1 else "s"))

    def selected():
        save = state["save"]
        if save is None:
            messagebox.showwarning("No save", "Load a save file first.")
            return None, None
        sel = lb.curselection()
        if not sel:
            messagebox.showwarning(
                "No selection", "Select an operator in the list first!")
            return save, None
        return save, save.characters[sel[0]]

    def do_export():
        save, c = selected()
        if not c:
            return
        default = safe_filename("%s %s" % (c["first"], c["last"])) + EXPORT_EXT
        out = filedialog.asksaveasfilename(
            title="Export operator", initialfile=default,
            defaultextension=EXPORT_EXT,
            filetypes=[("Zero Company operator", "*" + EXPORT_EXT)])
        if not out:
            return
        try:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(save.export_char(c), f, indent=1)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        status.set("Exported to %s" % out)

    def do_import():
        save = state["save"]
        if save is None:
            messagebox.showwarning("No save", "Load a save file first!")
            return
        p = filedialog.askopenfilename(
            title="Import operator",
            filetypes=[("Zero Company operator", "*" + EXPORT_EXT),
                       ("All files", "*.*")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            new_data = save.import_char(pkg)
            backup = save.write(new_data)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        refresh()
        messagebox.showinfo(
            "Imported",
            "Imported %s %s.\n\nBackup saved to:\n%s\n\nRestart the game to "
            "see the new operator!"
            % (pkg.get("first_name", "?"), pkg.get("last_name", ""), backup))

    def do_delete():
        save, c = selected()
        if not c:
            return
        name = ("%s %s" % (c["first"], c["last"])).strip()
        if not messagebox.askyesno(
                "Delete operator",
                "Remove %s from the save?\n(A backup is made first.)" % name):
            return
        try:
            backup = save.write(save.delete_char(c))
        except Exception as e:
            messagebox.showerror("Delete failed", str(e))
            return
        refresh()
        status.set("Deleted %s (backup: %s)" % (name, backup))

    brow = ttk.Frame(top)
    brow.pack(fill="x", pady=(6, 0))
    ttk.Button(brow, text="Refresh", command=refresh).pack(side="left")
    ttk.Button(brow, text="Export selected...", command=do_export).pack(
        side="left", padx=6)
    ttk.Button(brow, text="Import...", command=do_import).pack(side="left")
    ttk.Button(brow, text="Delete selected", command=do_delete).pack(
        side="left", padx=6)
    ttk.Label(
        top, foreground="gray",
        text="REMEMBER: Close the game before importing or deleting!").pack(
        anchor="w", pady=(6, 0))

    refresh()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
