# Building the Windows app

`Leadsmith.exe` is a single file. No installer, no Python on the machine, no
runtime to chase. Copy it to a folder it can write to and double-click it.

## Where the data lives

Beside the executable — `leads.db`, `config.json` and `sites/`. That is
deliberate: a one-file PyInstaller build unpacks itself to a temporary
directory that Windows deletes on exit, so anything written *inside* the app
would vanish the moment it closed. Keeping state next to the .exe also means
the whole thing — app, database, generated sites — copies to another machine on
a memory stick.

## Build it

**From GitHub Actions, which is the normal path.** Every push builds it on
`windows-latest` and attaches `Leadsmith.exe` to the run as an artifact; a
`v*` tag attaches it to the release. Nothing to install locally.

**On a Windows machine:**

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[build]"
pyinstaller build/leadsmith.spec --noconfirm
dist\Leadsmith.exe --selftest
```

`--selftest` builds every page and exits. It exists because PyInstaller's
failure mode is not a failed build — it is a perfectly good executable that
dies on launch because a module was imported by name and the static analysis
missed it. That looks like success until someone double-clicks it, so CI runs
it before publishing anything.

## Why it cannot be built on Linux or a Mac

PyInstaller does not cross-compile: it wraps the Python interpreter of the
machine it runs on, so a Windows .exe needs Windows. That is the entire reason
`.github/workflows/windows-app.yml` exists.

## Size

Roughly 45–70 MB. Most of that is Qt, and the spec already excludes the parts
this app never touches — QML, WebEngine, 3D, multimedia, charts, SQL, OpenGL —
which is worth about two thirds of an untrimmed build.

If that matters more than the look, the alternative is Tkinter: about 12 MB and
in the standard library, at the cost of widgets that look like 2009. The trade
was made the other way here because this is a tool someone opens every morning.

## Running from source

```
pip install -e ".[gui]"
python leadsmith_app.py
```

The CLI is unaffected and needs none of this: `pip install -e .` then
`leadsmith --help`. Both front ends call the same modules, so anything the app
does can also be scripted.
