fn main() {
    // ADR-0020 §7: tauri-build reads tauri.conf.json + capabilities/*.json
    // at build time and generates the context macros (`generate_context!`)
    // used by main.rs.
    tauri_build::build()
}
