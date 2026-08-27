// The command list is not decoration. Tauri v2 checks the ACL for every
// command coming from a non-local origin, and after a library is opened the
// window is on http://127.0.0.1:<port> -- remote. Declaring the commands here
// is what generates the `allow-<command>` permissions that
// capabilities/default.json then grants; without it the generated permission
// list is empty, every capability is powerless, and the invoke is rejected
// with "not allowed by ACL". Adding a #[tauri::command] without adding it
// here works from the launcher and fails from the app.
const COMMANDS: &[&str] = &[
    "home",
    "known",
    "configured",
    "pick_folder",
    "free_space",
    "has_library",
    "open_library",
    "back_to_chooser",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
