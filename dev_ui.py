"""Dev entry point — run with: flet run -d -r dev_ui.py"""
import os
os.environ["FLET_HIDE_WINDOW_ON_START"] = "1"

from voice_typer.ui.app import VoiceTyperApp
import flet as ft

if __name__ == "__main__":
    app = VoiceTyperApp()
    ft.run(app.main)