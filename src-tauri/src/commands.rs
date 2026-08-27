use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;

use crate::sidecar::{free_port, server_exe, wait_healthy, Sidecar};
use crate::state::AppState;

#[derive(Serialize)]
pub struct LibraryEntry {
    pub path: String,
    pub reachable: bool,
    #[serde(rename = "hasLibrary")]
    pub has_library: bool,
}

/// Must agree byte-for-byte with `appconfig.config_path()`, which uses
/// platformdirs' `user_config_dir("splitstep")`. Hardcoded rather than shelled
/// out to Python, because the chooser runs before any Python process exists --
/// that is the whole reason this layer is native.
fn config_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_default();
    PathBuf::from(home).join("Library/Application Support/splitstep/config.json")
}

fn read_config() -> serde_json::Value {
    std::fs::read_to_string(config_path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

fn write_config(cfg: &serde_json::Value) -> std::io::Result<()> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // Temp-file + rename, matching appconfig.save_config's atomicity. A torn
    // config is read by the chooser itself, so a half-written file would
    // break the one screen that can repair it.
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(cfg)?)?;
    std::fs::rename(tmp, path)
}

#[tauri::command]
pub fn home() -> String {
    std::env::var("HOME").unwrap_or_else(|_| "/".into())
}

#[tauri::command]
pub fn configured() -> Option<String> {
    read_config()
        .get("library")
        .and_then(|v| v.as_str())
        .map(str::to_string)
}

#[tauri::command]
pub fn known() -> Vec<LibraryEntry> {
    read_config()
        .get("libraries")
        .and_then(|v| v.as_array())
        .map(|paths| {
            paths
                .iter()
                .filter_map(|v| v.as_str())
                .map(|path| LibraryEntry {
                    path: path.to_string(),
                    // "Reachable" is the parent directory existing, not the
                    // library.db: a mounted drive whose library was deleted
                    // is a different problem from an unplugged drive, and
                    // only the second is fixed by plugging something in.
                    reachable: Path::new(path).is_dir(),
                    has_library: Path::new(path).join("library.db").is_file(),
                })
                .collect()
        })
        .unwrap_or_default()
}

#[tauri::command]
pub fn has_library(path: String) -> bool {
    Path::new(&path).join("library.db").is_file()
}

#[tauri::command]
pub fn free_space(path: String) -> Option<u64> {
    // statvfs needs a path that exists, but the whole point is describing a
    // folder we have not created yet -- so walk up to the nearest existing
    // ancestor, which is on the same volume.
    let mut probe = PathBuf::from(&path);
    while !probe.exists() {
        probe = probe.parent()?.to_path_buf();
    }
    let c_path = std::ffi::CString::new(probe.to_string_lossy().as_bytes()).ok()?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(c_path.as_ptr(), &mut stat) } != 0 {
        return None;
    }
    Some(stat.f_bavail as u64 * stat.f_frsize as u64)
}

#[tauri::command]
pub async fn pick_folder(app: tauri::AppHandle) -> Option<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_folder(move |picked| {
        let _ = tx.send(picked);
    });
    rx.recv()
        .ok()
        .flatten()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
pub fn open_library(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    path: String,
    create: bool,
) -> Result<(), String> {
    let library = PathBuf::from(&path);
    if create {
        std::fs::create_dir_all(&library).map_err(|e| e.to_string())?;
    } else if !library.is_dir() {
        return Err(format!("{path} is not available."));
    }

    let resource_dir = app.path().resource_dir().ok();
    let exe = server_exe(resource_dir.as_deref());

    // Three attempts, each on a freshly assigned port: the only realistic
    // failure is losing the free-port race, and a new port is the fix.
    let mut last_error = String::new();
    for _ in 0..3 {
        let port = free_port().map_err(|e| e.to_string())?;
        match Sidecar::spawn(&exe, &library, create, port) {
            Ok(mut child) => {
                if wait_healthy(port, Duration::from_secs(30)) {
                    persist(&path, create)?;
                    let window = app.get_webview_window("main").ok_or("no main window")?;
                    let url = format!("http://127.0.0.1:{port}/");
                    window
                        .navigate(url.parse().map_err(|_| "bad url")?)
                        .map_err(|e| e.to_string())?;
                    *state.sidecar.lock().unwrap() = Some(child);
                    return Ok(());
                }
                last_error = drain_stderr(&mut child);
                child.terminate();
            }
            Err(e) => last_error = e.to_string(),
        }
    }
    Err(if last_error.is_empty() {
        "SplitStep could not start its server.".into()
    } else {
        format!("SplitStep could not start its server. {last_error}")
    })
}

fn drain_stderr(child: &mut Sidecar) -> String {
    use std::io::Read;
    let Some(mut stderr) = child.child.stderr.take() else {
        return String::new();
    };
    let mut buf = String::new();
    let _ = stderr.read_to_string(&mut buf);
    // The last few lines carry the actual failure; the rest is uvicorn noise.
    buf.lines()
        .filter(|line| !line.trim().is_empty())
        .rev()
        .take(3)
        .collect::<Vec<_>>()
        .join(" ")
}

fn persist(path: &str, create: bool) -> Result<(), String> {
    let mut cfg = read_config();
    cfg["library"] = serde_json::json!(path);
    // Friend mode is written on the launch that creates a library and never
    // after: a developer who later flips the Settings toggle must not have it
    // flipped back every time they reopen their library.
    if create {
        cfg["mode"] = serde_json::json!("friend");
    }
    let mut list: Vec<String> = known()
        .into_iter()
        .map(|e| e.path)
        .filter(|p| p != path)
        .collect();
    list.insert(0, path.to_string());
    cfg["libraries"] = serde_json::json!(list);
    write_config(&cfg).map_err(|e| e.to_string())
}

/// Boot straight into a configured, reachable library.
///
/// Returns false when the chooser must be shown instead: nothing configured,
/// or the configured path is not a directory right now -- which is the
/// unplugged-drive case, and the one the chooser's "not connected" banner
/// exists for. A configured library whose server fails to start also falls
/// through to the chooser, where the error is visible and fixable.
pub fn autoboot(app: &tauri::AppHandle) -> bool {
    let Some(path) = configured() else {
        return false;
    };
    if !Path::new(&path).is_dir() {
        return false;
    }
    let state = app.state::<AppState>();
    open_library(app.clone(), state, path, false).is_ok()
}

/// Tear the sidecar down and return to the chooser.
///
/// Re-point, never move: the library being left is untouched on disk and is
/// already in `libraries`, so coming back to it is one click. That is the
/// whole contract of changing location.
#[tauri::command]
pub fn back_to_chooser(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    if let Some(mut sidecar) = state.sidecar.lock().unwrap().take() {
        sidecar.terminate();
    }
    let window = app.get_webview_window("main").ok_or("no main window")?;
    window
        .navigate(
            "tauri://localhost/launcher.html"
                .parse()
                .map_err(|_| "bad url")?,
        )
        .map_err(|e| e.to_string())
}
