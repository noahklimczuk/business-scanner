# Building the Windows app

`Leadsmith.exe` is a single file. No installer, no Python on the machine, no
runtime to chase. Copy it to a folder it can write to and double-click it.

## Where the data lives

Beside the executable — `leads.db`, `config.json`, `sites/` and `invoices/`.
That is deliberate: a one-file PyInstaller build unpacks itself to a temporary
directory that Windows deletes on exit, so anything written *inside* the app
would vanish the moment it closed. Keeping state next to the .exe also means
the whole thing — app, database, generated sites, invoices — copies to another
machine on a memory stick.

Back up `leads.db` and `invoices/` together. The database is the record of what
was billed and paid; the folder holds the pages that were actually handed over.

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

## Releasing

1. Bump `VERSION` in `version.py`. That is the only place the number lives —
   `pyproject.toml` reads it, and a running copy compares it against the newest
   release to decide whether to offer an update.
2. Merge to `main`.
3. Tag `v<version>` on the merge commit, or run the workflow by hand with
   `release_tag` set. Either one builds the .exe and attaches it to the release.

**The tag and `version.py` have to agree.** A tag ahead of `version.py` makes
every installed copy offer an update it is already running, forever. A tag
behind it hides a real one.

## Updating in place

An installed copy asks GitHub for the newest release a second or two after it
opens, and puts a bar across the top of the window when there is one. Pressing
Install downloads the .exe beside the running one, checks it against the SHA-256
GitHub publishes for that asset, renames the running build to
`Leadsmith.exe.old` and moves the new file into its place.

Three things are deliberate and worth not undoing:

- **The hash is checked before anything is replaced.** A download that does not
  match is deleted and the working copy is untouched. An asset with no published
  digest is not installed at all.
- **The downloaded file is never executed.** The app asks the operator to close
  and reopen it; the `.old` is cleared on the next launch. That keeps "we ran
  what we just downloaded" out of the design entirely.
- **The check cannot interrupt anything.** No network, a rate limit or a captive
  portal all return "no update" silently, and the check runs off the job queue
  that serialises scans, so it can never block one or be blocked by one.

Running from source there is no single file to swap, so the bar links to the
release notes instead and the update is `git pull`.

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
