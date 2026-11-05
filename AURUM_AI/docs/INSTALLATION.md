# Module 05 installation

Run from `AURUM_AI` in PowerShell, with Modules 01-04 already set up.

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m aurum.cli init-db        # applies migration 0007 and any earlier ones
python -m aurum.cli decide check   # reports what each instrument has and lacks
```

`init-db` only adds tables. Then start the backend and frontend as usual:

```powershell
python -m aurum.api
cd ..\aurum-frontend; npm install; npm run dev
```

To record decisions from the browser, turn demo-data mode off and enter the admin key in Settings > Security. The key
stays in memory and is gone on reload.

AI review is optional. See [CONFIGURATION.md](CONFIGURATION.md). Everything else works without it.
