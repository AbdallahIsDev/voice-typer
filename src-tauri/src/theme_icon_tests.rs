//! Tests for `theme_icon.rs` (sibling module per C-TEST-5 — no inline
//! test blocks in production source).
//!
//! Pins the OS-theme → glyph contract without touching a real window:
//! mapping direction, asset decodability, and the 512×512 RGBA shape
//! `set_icon` relies on.

use tauri::Theme;

use crate::theme_icon::{image_for_theme, png_for_theme};

#[test]
fn dark_os_theme_selects_white_glyph() {
    let png = png_for_theme(&Theme::Dark);
    assert!(!png.is_empty(), "white icon bytes must be embedded");
    // PNG magic — guards against an `include_bytes!` path typo that
    // would otherwise surface only as a runtime decode failure.
    assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
}

#[test]
fn light_os_theme_selects_black_glyph() {
    let png = png_for_theme(&Theme::Light);
    assert!(!png.is_empty(), "black icon bytes must be embedded");
    assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
}

#[test]
fn themes_select_different_glyphs() {
    assert_ne!(
        png_for_theme(&Theme::Dark) as *const [u8],
        png_for_theme(&Theme::Light) as *const [u8],
        "dark/light must not resolve to the same embedded asset"
    );
}

#[test]
fn both_glyphs_decode_to_512_rgba() {
    for theme in [Theme::Dark, Theme::Light] {
        let img = image_for_theme(&theme)
            .unwrap_or_else(|e| panic!("{theme:?} icon must decode: {e}"));
        assert_eq!(img.width(), 512, "{theme:?} width");
        assert_eq!(img.height(), 512, "{theme:?} height");
        assert_eq!(
            img.rgba().len(),
            512 * 512 * 4,
            "{theme:?} must be full RGBA"
        );
    }
}

#[test]
fn dark_glyph_is_light_and_light_glyph_is_dark() {
    // Mean-luma guard on the opaque pixels: the dark-OS glyph must read
    // light (white bars) and the light-OS glyph dark (black bars). A
    // swapped/identical pair fails here even if it decodes fine.
    for (theme, expect_light) in [(Theme::Dark, true), (Theme::Light, false)] {
        let img = image_for_theme(&theme).expect("decodes");
        let rgba = img.rgba();
        let (mut sum, mut n) = (0u64, 0u64);
        for px in rgba.chunks_exact(4) {
            if px[3] > 128 {
                sum += (2126 * u64::from(px[0])
                    + 7152 * u64::from(px[1])
                    + 722 * u64::from(px[2]))
                    / 10000;
                n += 1;
            }
        }
        assert!(n > 10_000, "{theme:?} glyph must have opaque pixels");
        let mean = sum / n;
        if expect_light {
            assert!(mean > 200, "{theme:?} glyph too dark (mean luma {mean})");
        } else {
            assert!(mean < 55, "{theme:?} glyph too light (mean luma {mean})");
        }
    }
}
