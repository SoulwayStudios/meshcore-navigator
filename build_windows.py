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
        "--collect-all=PyQt6.QtWebEngineCore",
        "--collect-all=PyQt6.QtWebEngineWidgets",
        "--collect-submodules=PyQt6",
        "--hidden-import=PyQt6.QtWebEngineWidgets",
        "--hidden-import=PyQt6.QtWebEngineCore",
        "--hidden-import=PyQt6.QtWebChannel",
        "--hidden-import=PyQt6.QtCore",
        "--hidden-import=PyQt6.QtGui",
        "--hidden-import=PyQt6.QtWidgets",
        "--hidden-import=PyQt6.QtNetwork",
        "--hidden-import=qasync",
        "--hidden-import=serial",
        "--collect-all=Crypto",
        "--collect-all=paho",
        "--hidden-import=paho.mqtt.client",
        "--hidden-import=Crypto.Cipher.AES",
        "--hidden-import=Crypto.Hash.HMAC",
        "--hidden-import=Crypto.Hash.SHA256",
        "--hidden-import=serial.tools",
        "--hidden-import=serial.tools.list_ports",
    ]

    print("Building Windows binary with PyInstaller...")
    print("Arguments:", args)
    PyInstaller.__main__.run(args)

    dist_dir = os.path.join(script_dir, "dist", "MESHCORE-NAVIGATOR")
    exe_without_hyphen = os.path.join(dist_dir, "MESHCORENAVIGATOR.exe")
    if os.path.exists(exe_without_hyphen):
        print(f"Removing duplicate binary alias: {exe_without_hyphen}")
        os.remove(exe_without_hyphen)

    print("Windows build completed successfully.")

if __name__ == "__main__":
    build()
