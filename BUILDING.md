# Building the .exe from source

The distributed `ZeroCompanyOperatorShare.exe` is the single Python source
file `zc_operators.py` bundled with PyInstaller. Anyone can reproduce an
equivalent binary from this repository.

## Build environment (release build)

| Component   | Version                          |
| ----------- | -------------------------------- |
| OS          | Windows 11 Home (10.0.26200)     |
| Python      | 3.13.3 (python.org, 64-bit)      |
| PyInstaller | 6.19.0                           |

The tool itself uses only the Python standard library (tkinter GUI); there
are no third-party runtime dependencies.

## Build command

```powershell
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "ZeroCompanyOperatorShare" zc_operators.py
```

or just run `build.ps1`. The exe appears in `dist\`.

- `--onefile` packs the Python interpreter, tkinter and the script into one
  self-extracting executable.
- `--windowed` builds a GUI app with no console window.

## About antivirus false positives

PyInstaller one-file executables are self-extracting archives that unpack a
Python interpreter to a temp directory at launch. Some antivirus engines
flag this generic pattern heuristically, which is a well-known false-positive
issue for PyInstaller (see
<https://github.com/pyinstaller/pyinstaller/issues?q=false+positive>).
The binary is unsigned; verify a download against the release notes, which
list the SHA-256 of each released exe, or build it yourself with the two
commands above.

## What the program does (scope)

- Reads and writes exactly one file, the game's character databank save
  (`%LOCALAPPDATA%\SWZeroCompany\Saved\SaveGames\CharacterDatabank_Default_Custom_Characters.sav`),
  or a `.sav` / `.zcop` path the user explicitly picks in a file dialog.
- Makes a timestamped backup before any write and validates the new file by
  re-parsing it before replacing the save.
- No network access of any kind, no registry access, no other file access.
  This is auditable in the single source file.
