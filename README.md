A tiny tool for Star Wars: Zero Company that lets you view, export and import your custom Operators, so you can share them with friends.

Operators are exported as small plain JSON files which I named .zcop. In order to share an Operator with someone just send them this file, and they can then import it with the same tool and the Operator then shows up in their custom character pool.

# How To Use
- Run the .exe; it will automatically find your CharacterDatabank_Default_Custom_Characters.sav file and load your list of custom operators, which allows you to import, delete and export your characters at will.

# Known Issues
- Character thumbnails are a little wonky on imported characters; they stabilize after a little bit so don't be alarmed lmao
- No idea how this will work when we get modding tools to add custom stuff to character creation, so YMMV when that happens.

# Safety Warnings
- Close the game before importing or deleting! The game rewrites the databank on save and would clobber your change.
- Every import/delete first writes a timestamped copy of your save into the Backups folder next to the tool. To undo anything, copy the backup over the .sav file to restore!
- Exports are tied to the game's save format (UE 5.6 GVAS). If a game update for some reason changes this format, you will likely need to wait for a tool update in order to safely re-export and re-import your operators.

# How It Works (Because I Was Curious)
It's pretty easy to find the file that custom operators get stored in because if you hover over their name in the databank in game, it literally tells you the 'folder' name. This tool parses just enough of the property stream in that .sav file to find each Operator's byte ranges, then exports them verbatim. Import will just splice the bytes back in, rewrites their GUID to avoid conflicts, and fixes up the affected counts and size fields. That's literally it, it's just fancy copy/paste.
