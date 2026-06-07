"""Dev entry point — run with: flet run -d -r dev_ui.py"""
from voice_typer.ui.app import VoiceTyperApp
import flet as ft

if __name__ == "__main__":
    app = VoiceTyperApp()
    ft.run(app.main)
