"""Window resize + double-click maximize using pure Flet controls.

Edge/corner resize via invisible GestureDetector strips.
Double-click maximize via GestureDetector on the title bar zone.
"""

import flet as ft
import logging

log = logging.getLogger(__name__)

RESIZE = 5


def _on_top_pan(e):
    p = e.control.page
    p.window.top += e.delta_y
    p.window.height -= e.delta_y
    p.update()

def _on_bottom_pan(e):
    p = e.control.page
    p.window.height += e.delta_y
    p.update()

def _on_left_pan(e):
    p = e.control.page
    p.window.left += e.delta_x
    p.window.width -= e.delta_x
    p.update()

def _on_right_pan(e):
    p = e.control.page
    p.window.width += e.delta_x
    p.update()

def _on_tl_pan(e):
    p = e.control.page
    p.window.top += e.delta_y
    p.window.height -= e.delta_y
    p.window.left += e.delta_x
    p.window.width -= e.delta_x
    p.update()

def _on_tr_pan(e):
    p = e.control.page
    p.window.top += e.delta_y
    p.window.height -= e.delta_y
    p.window.width += e.delta_x
    p.update()

def _on_bl_pan(e):
    p = e.control.page
    p.window.height += e.delta_y
    p.window.left += e.delta_x
    p.window.width -= e.delta_x
    p.update()

def _on_br_pan(e):
    p = e.control.page
    p.window.height += e.delta_y
    p.window.width += e.delta_x
    p.update()


def wrap_with_resize_overlay(content: ft.Control,
                              on_double_tap_title=None) -> ft.Stack:
    """Wrap *content* in a Stack with invisible resize handles.

    Returns a Stack with:
      [0] = the actual content
      [1+] = transparent resize strips
    """
    top_gd = ft.GestureDetector(
        content=ft.Container(height=RESIZE, expand=True),
        on_pan_update=_on_top_pan,
        on_double_tap=on_double_tap_title,
    )
    bottom_gd = ft.GestureDetector(
        content=ft.Container(height=RESIZE, expand=True),
        on_pan_update=_on_bottom_pan,
    )
    left_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, expand=True),
        on_pan_update=_on_left_pan,
    )
    right_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, expand=True),
        on_pan_update=_on_right_pan,
    )
    tl_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE),
        on_pan_update=_on_tl_pan,
    )
    tr_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE),
        on_pan_update=_on_tr_pan,
    )
    bl_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE),
        on_pan_update=_on_bl_pan,
    )
    br_gd = ft.GestureDetector(
        content=ft.Container(width=RESIZE, height=RESIZE),
        on_pan_update=_on_br_pan,
    )

    return ft.Stack(
        [
            content,
            # Edge strips
            ft.Container(
                content=ft.Column([top_gd], horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                left=0, top=0, right=0, height=RESIZE,
            ),
            ft.Container(
                content=ft.Column([bottom_gd], horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                left=0, bottom=0, right=0, height=RESIZE,
            ),
            ft.Container(
                content=ft.Row([left_gd], vertical_alignment=ft.CrossAxisAlignment.STRETCH),
                left=0, top=0, bottom=0, width=RESIZE,
            ),
            ft.Container(
                content=ft.Row([right_gd], vertical_alignment=ft.CrossAxisAlignment.STRETCH),
                right=0, top=0, bottom=0, width=RESIZE,
            ),
            # Corner squares
            ft.Container(content=tl_gd, left=0, top=0),
            ft.Container(content=tr_gd, right=0, top=0),
            ft.Container(content=bl_gd, left=0, bottom=0),
            ft.Container(content=br_gd, right=0, bottom=0),
            # Title bar double-click zone (top 40px, excluding right 140px for buttons)
            ft.Container(
                content=ft.GestureDetector(
                    content=ft.Container(expand=True),
                    on_double_tap=on_double_tap_title,
                ),
                left=0, top=0, right=140, height=40,
            ),
        ],
        expand=True,
    )