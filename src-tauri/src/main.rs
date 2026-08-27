#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod lifecycle;
mod sidecar;
mod state;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

use crate::state::AppState;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // A second launch focuses the window we already have rather than
            // starting a second worker against one library.db -- the enqueue
            // race jobs/handlers.py names. This is the first line of defence;
            // Phase 1's fix is the second.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(AppState::default())
        .setup(|app| {
            // Built here rather than declared in tauri.conf.json because
            // initialization_script -- the only injection that survives a
            // navigation -- exists only on the builder. The bridge has to
            // outlive the jump to the Python server, or the app tier loses
            // Change library.
            //
            // Hidden at first: autoboot navigates away before anyone sees the
            // chooser, so showing it up front would flash a screen the
            // returning user never chose to visit.
            let window = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::App("launcher.html".into()),
            )
            .title("SplitStep")
            .inner_size(900.0, 660.0)
            .min_inner_size(720.0, 520.0)
            .resizable(true)
            .visible(false)
            .initialization_script(include_str!("bridge.js"))
            .build()?;

            // Before anything spawns: a previous run that was force-quit or
            // crashed may have left its server alive, holding library.db.
            sidecar::reap_orphan();

            lifecycle::watch(app.handle().clone());

            // On a thread: autoboot blocks for up to thirty seconds waiting
            // on the health check, and blocking setup() means a beachball
            // instead of a window.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                commands::autoboot(&handle);
                let _ = window.show();
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::home,
            commands::known,
            commands::configured,
            commands::pick_folder,
            commands::free_space,
            commands::has_library,
            commands::open_library,
            commands::back_to_chooser,
        ])
        .build(tauri::generate_context!())
        .expect("error while building SplitStep")
        .run(|app, event| {
            // Both, not just ExitRequested. Quitting the installed .app left
            // the sidecar running and the pidfile behind -- measured, not
            // assumed -- because the quit path that actually fired reached
            // Exit without ExitRequested ever being observed. shutdown() is
            // idempotent (it take()s the sidecar out of the state), so
            // handling both costs nothing and closes the gap.
            //
            // The reaper still exists for the paths no event can cover at
            // all: SIGKILL and a crash.
            match event {
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                    lifecycle::shutdown(app);
                }
                _ => {}
            }
        });
}
