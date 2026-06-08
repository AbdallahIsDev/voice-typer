"""Minimal test to verify Flet resize handles work.

Run with: python -m voice_typer.ui.test_resize
"""

import flet as ft

RESIZE = 5


def _on_top(e):
    if p := e.control.page:
        p.window.top += e.delta_y
        p.window.height -= e.delta_y
        print(f"[TOP] dy={e.delta_y:.0f}  top={p.window.top} h={p.window.height}")
        p.update()

def _on_bottom(e):
    if p := e.control.page:
        p.window.height += e.delta_y
        print(f"[BOT] dy={e.delta_y:.0f}  h={p.window.height}")
        p.update()

def _on_left(e):
    if p := e.control.page:
        p.window.left += e.delta_x
        p.window.width -= e.delta_x
        print(f"[LEFT] dx={e.delta_x:.0f}  left={p.window.left} w={p.window.width}")
        p.update()

def _on_right(e):
    if p := e.control.page:
        p.window.width += e.delta_x
        print(f"[RIGHT] dx={e.delta_x:.0f}  w={p.window.width}")
        p.update()

def _on_tl(e):
    if p := e.control.page:
        p.window.top += e.delta_y
        p.window.height -= e.delta_y
        p.window.left += e.delta_x
        p.window.width -= e.delta_x
        print(f"[TL] dx={e.delta_x:.0f} dy={e.delta_y:.0f}")
        p.update()

def _on_tr(e):
    if p := e.control.page:
        p.window.top += e.delta_y
        p.window.height -= e.delta_y
        p.window.width += e.delta_x
        print(f"[TR] dx={e.delta_x:.0f} dy={e.delta_y:.0f}")
        p.update()

def _on_bl(e):
    if p := e.control.page:
        p.window.height += e.delta_y
        p.window.left += e.delta_x
        p.window.width -= e.delta_x
        print(f"[BL] dx={e.delta_x:.0f} dy={e.delta_y:.0f}")
        p.update()

def _on_br(e):
    if p := e.control.page:
        p.window.height += e.delta_y
        p.window.width += e.delta_x
        print(f"[BR] dx={e.delta_x:.0f} dy={e.delta_y:.0f}")
        p.update()


def _toggle_max(page):
    target = not page.window.maximized
    page.window.maximized = target
    print(f"[MAX] {'maximized' if target else 'restored'}")
    page.update()


def main(page: ft.Page):
    page.title = "Resize Test"
    page.window.width = 600
    page.window.height = 400
    page.window.frameless = True

    top_gd = ft.GestureDetector(
        content=ft.Container(height=RESIZE, expand=True, bgcolor="red"),
        on_pan_update=_on_top,
        on_double_tap=lambda e: _toggle_max(page),
    )
    bottom_gd = ft.GestureDetector(
        content=ft.Container(height=RESIZE, expand=True, bgcolor="green"),
        on_pan_update=_on_bottom,
    )
    left_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, expand=True, bgcolor="blue"),
        on_pan_update=_on_left,
    )
    right_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, expand=True, bgcolor="yellow"),
        on_pan_update=_on_right,
    )
    tl_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE, bgcolor="purple"),
        on_pan_update=_on_tl,
    )
    tr_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE, bgcolor="orange"),
        on_pan_update=_on_tr,
    )
    bl_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE, bgcolor="cyan"),
        on_pan_update=_on_bl,
    )
    br_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE, bgcolor="magenta"),
        on_pan_update=_on_br,
    )

    stack = ft.Stack([
        ft.Container(
            content=ft.Column([
                ft.WindowDragArea(
                    ft.Container(
                        content=ft.Row([
                            ft.Text("Drag here", color="white", size=20),
                        ], alignment=ft.MainAxisAlignment.CENTER),
                        height=40,
                        bgcolor="#333333",
                    ),
                    expand=True,
                ),
                ft.Container(expand=True, bgcolor="#222222"),
            ]),
            expand=True,
        ),
        # Edge strips (colored for testing)
        ft.Container(content=ft.Column([top_gd]), left=0, top=0, right=0, height=RESIZE),
        ft.Container(content=ft.Column([bottom_gd]), left=0, bottom=0, right=0, height=RESIZE),
        ft.Container(content=ft.Row([left_gd]), left=0, top=0, bottom=0, width=RESIZE),
        ft.Container(content=ft.Row([right_gd]), right=0, top=0, bottom=0, width=RESIZE),
        # Corners
        ft.Container(content=tl_gd, left=0, top=0),
        ft.Container(content=tr_gd, right=0, top=0),
        ft.Container(content=bl_gd, left=0, bottom=0),
        ft.Container(content=br_gd, right=0, bottom=0),
        # Title double-click zone
        ft.Container(
            content=ft.GestureDetector(
                content=ft.Container(expand=True),
                on_double_tap=lambda e: _toggle_max(page),
            ),
            left=0, top=0, right=140, height=40,
        ),
    ], expand=True)

    page.add(stack)
    page.update()


if __name__ == "__main__":
    ft.run(main)