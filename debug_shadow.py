import msgpack
import flet as ft
from flet.messaging.protocol import configure_encode_object_for_msgpack
from flet.controls.control import Control

encoder = configure_encode_object_for_msgpack(Control)

c = ft.Container(
    expand=True,
    shadow=ft.BoxShadow(
        blur_radius=20, color="#4D000000",
        offset=ft.Offset(0, 8), spread_radius=2,
    ),
)
packed = msgpack.packb(encoder(c), default=encoder)
unpacked = msgpack.unpackb(packed, ext_hook=ft.messaging.protocol.decode_ext_from_msgpack)
print("shadow:", unpacked["shadow"])
