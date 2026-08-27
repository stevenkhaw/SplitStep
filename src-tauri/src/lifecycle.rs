//! One poller answering three questions that share an answer.

use std::process::{Child, Command, Stdio};
use std::time::Duration;

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_notification::NotificationExt;

use crate::state::AppState;

/// Poll /api/jobs and act on it.
///
/// The power assertion and the finished-detect notification need the same
/// answer to the same question -- are any jobs pending -- so they share one
/// poller rather than each running their own. Five seconds is chosen against
/// a fifteen-minute detect: fast enough that the notification feels
/// immediate, slow enough to be free.
pub fn watch(app: AppHandle) {
    std::thread::spawn(move || {
        let mut caffeinate: Option<Child> = None;
        let mut was_detecting = false;
        loop {
            std::thread::sleep(Duration::from_secs(5));

            let port = {
                let state = app.state::<AppState>();
                let mut guard = state.sidecar.lock().unwrap();
                match guard.as_mut() {
                    Some(sidecar) => {
                        // try_wait returning Ok(Some(_)) means it exited.
                        if matches!(sidecar.child.try_wait(), Ok(Some(_))) {
                            drop(guard);
                            release(&mut caffeinate);
                            offer_relaunch(&app);
                            return;
                        }
                        sidecar.port
                    }
                    // No sidecar means the chooser is up: nothing to poll,
                    // and no assertion worth holding.
                    None => {
                        drop(guard);
                        release(&mut caffeinate);
                        continue;
                    }
                }
            };

            let Ok(body) = ureq::get(&format!("http://127.0.0.1:{port}/api/jobs"))
                .timeout(Duration::from_secs(3))
                .call()
                .and_then(|r| r.into_string().map_err(Into::into))
            else {
                continue;
            };

            let pending = body.contains("\"queued\"") || body.contains("\"running\"");
            let detecting = pending && body.contains("\"detect\"");

            // caffeinate -s rather than an IOKit assertion: it is a system
            // binary on every Mac, it dies with us if we crash, and it needs
            // no objc FFI in a shell that is deliberately thin.
            if pending && caffeinate.is_none() {
                caffeinate = Command::new("/usr/bin/caffeinate")
                    .arg("-s")
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()
                    .ok();
            } else if !pending {
                release(&mut caffeinate);
            }

            if was_detecting && !detecting {
                let _ = app
                    .notification()
                    .builder()
                    .title("Detection finished")
                    .body("Your rallies are ready to review.")
                    .show();
            }
            was_detecting = detecting;
        }
    });
}

fn release(caffeinate: &mut Option<Child>) {
    if let Some(mut child) = caffeinate.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Without this a crashed Python process leaves a blank window and no
/// explanation, which reads as "the app is broken" with nowhere to go.
fn offer_relaunch(app: &AppHandle) {
    let handle = app.clone();
    app.dialog()
        .message("SplitStep's server stopped unexpectedly.")
        .title("SplitStep")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Relaunch".into(),
            "Quit".into(),
        ))
        .show(move |relaunch| {
            if relaunch {
                handle.restart();
            } else {
                handle.exit(0);
            }
        });
}

/// SIGTERM on quit. Jobs are idempotent and `reclaim_stale` covers a hard
/// kill, so this is about closing sqlite cleanly, not about correctness.
pub fn shutdown(app: &AppHandle) {
    let state = app.state::<AppState>();
    // Taken in its own statement: an if-let scrutinee's temporaries live to
    // the end of the block, so the MutexGuard would outlive `state` itself.
    let running = state.sidecar.lock().unwrap().take();
    if let Some(mut sidecar) = running {
        sidecar.terminate();
    }
}
