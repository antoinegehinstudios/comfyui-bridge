@echo off
rem Lance la passerelle ComfyUI (port 8077), detachee, et journalise dans _data\bridge.log.
rem PYTHONUTF8 est obligatoire ici : sans lui le demarrage casse sur les accents.
cd /d "E:\Projets Cortex\Banc socles\comfyui-bridge"
set PYTHONUTF8=1
if not exist "_data" mkdir "_data"
".venv\Scripts\python.exe" -m uvicorn comfyui_bridge.api.main:app --host 127.0.0.1 --port 8077 >> "_data\bridge.log" 2>&1
