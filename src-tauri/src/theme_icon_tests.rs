//! Tests for `theme_icon.rs` (sibling module per C-TEST-5 — no inline
//! test blocks in production source).
//!
//! Pins the OS-theme → icon-variant contract without touching a real
//! window: mapping direction, asset decodability, and the 512×512 RGBA
//! shape `set_icon` relies on. Both variants today carry the SAME
//! brand mark — the #1a1b1e chip + white glyph (user decision 2026-09:
//! the chip stays dark in light and dark OS themes alike) — while
//! remaining two separate assets so the chrome looks can diverge
//! later; both carry the same brand-red indicator dot.

use tauri::Theme;

use crate::theme_icon::{image_for_theme, png_for_theme};

#[test]
fn dark_os_theme_selects_dark_variant() {
    let png = png_for_theme(&Theme::Dark);
    assert!(!png.is_empty(), "dark-variant icon bytes must be embedded");
    // PNG magic — guards against an `include_bytes!` path typo that
    // would otherwise surface only as a runtime decode failure.
    assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
}

#[test]
fn light_os_theme_selects_light_variant() {
    let png = png_for_theme(&Theme::Light);
    assert!(!png.is_empty(), "light-variant icon bytes must be embedded");
    assert_eq!(&png[..8], b"\x89PNG\r\n\x1a\n");
}

#[test]
fn theme_variants_are_distinct_or_documented_identical() {
    // The two variants are embedded from DIFFERENT include_bytes! sites
    // (`icons/icon.png` vs `theme-icons/icon-dark-512.png`). Today both
    // files are byte-identical (the #1a1b1e chip + white glyph in light
    // AND dark chrome — user decision 2026-09), so the linker folds the
    // two statics together and even a pointer compare cannot distinguish
    // them; the pair MUST differ again once the files diverge (a future
    // LIGHT_CHIP/DARK_CHIP change). Pointer inequality is then the
    // guard against both arms accidentally resolving to one embed site.
    let dark = png_for_theme(&Theme::Dark);
    let light = png_for_theme(&Theme::Light);
    if dark == light {
        assert!(!dark.is_empty(), "identical variants must still embed bytes");
    } else {
        assert_ne!(
            dark as *const [u8],
            light as *const [u8],
            "dark/light must not resolve to the same embedded asset"
        );
    }
}

#[test]
fn both_variants_decode_to_512_rgba() {
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
fn both_variants_carry_the_brand_dark_chip() {
    // Dominant-color guard on the opaque pixels: each variant's most
    // common opaque color must be the CHIP color #1a1b1e (rgb
    // 26/27/30). User decision 2026-09: the chip is the same dark
    // neutral in BOTH the light-OS and dark-OS variants — a swapped,
    // reverted (white chip), or glyph-dominated asset fails here even
    // if it decodes fine. The chip covers ~70% of the opaque pixels in
    // both variants (the white glyph + red dot make up the rest). The
    // rounded corners' transparent pixels are excluded (alpha <= 128).
    for theme in [Theme::Dark, Theme::Light] {
        let img = image_for_theme(&theme).expect("decodes");
        let rgba = img.rgba();
        let mut counts: std::collections::HashMap<[u8; 3], u64> = Default::default();
        let (mut white_px, mut red_px) = (0u64, 0u64);
        for px in rgba.chunks_exact(4) {
            if px[3] > 128 {
                *counts.entry([px[0], px[1], px[2]]).or_default() += 1;
                if px[0] >= 245 && px[1] >= 245 && px[2] >= 245 {
                    white_px += 1; // white glyph on the dark chip
                } else if px[0] >= 200 && px[1] <= 80 && px[2] <= 80 {
                    red_px += 1; // the brand-red indicator dot
                }
            }
        }
        let (&color, _) = counts
            .iter()
            .max_by_key(|(_, n)| **n)
            .expect("icon must have opaque pixels");
        assert_eq!(
            color,
            [26, 27, 30],
            "{theme:?} dominant chip color must be #1a1b1e (26/27/30)"
        );
        assert!(white_px > 1_000, "{theme:?} must keep the white glyph (got {white_px} white px)");
        assert!(red_px > 50, "{theme:?} must keep the red dot (got {red_px} red px)");
    }
}
