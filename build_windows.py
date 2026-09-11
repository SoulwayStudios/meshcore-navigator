#!/usr/bin/env python3
"""Build script for Windows standalone binary using PyInstaller."""
import os
import sys
import PyInstaller.__main__

def build():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    static_data = f"meshcore_tray/ui/static{os.pathsep}meshcore_tray/ui/static"

    args = [
        "meshcore_tray/main.py",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name=MESHCORE-NAVIGATOR",
        "--icon=meshcore_tray/ui/static/icon.ico",
        f"--add-data={static_data}",
        "--collect-all=meshcore",
        "--collect-all=pixoo",
        "--collect-all=meshcore_tray",
        "--hidden-import=PyQt6.QtWebEngineWidgets",
        "--hidden-import=PyQt6.QtWebChannel",
        "--hidden-import=qasync",
        "--hidden-import=serial",
    ]

    print("Building Windows binary with PyInstaller...")
    print("Arguments:", args)
    PyInstaller.__main__.run(args)
    print("Windows build completed successfully.")

if __name__ == "__main__":
    build()
