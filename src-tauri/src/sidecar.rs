//! Spawning and health-checking the frozen Python server.

use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

/// Ask the OS for a port nobody is using.
///
/// Binding :0 and immediately dropping the listener leaves a microsecond
/// window where something else could take the port. That is tolerated rather
/// than solved with a stdout handshake, because the alternative means adding
/// shell-specific behaviour to a Python startup path that has none, and
/// `wait_healthy` failing is already the retry signal. A fixed port was
/// rejected outright: 8420 is where a developer's own `splitstep serve`
/// lives, and colliding with it on the one machine guaranteed to run both is
/// the worst possible default.
pub fn free_port() -> io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

pub struct Sidecar {
    pub child: Child,
    pub port: u16,
}

impl Sidecar {
    pub fn spawn(exe: &Path, library: &Path, create: bool, port: u16) -> io::Result<Self> {
        let mut cmd = Command::new(exe);
        cmd.arg("--library")
            .arg(library)
            .arg("--port")
            .arg(port.to_string());
        if create {
            // Only ever on the launch that creates a library. `serve
            // --create` refuses a path resolved from the env or the config
            // file, so it is always paired with an explicit --library here.
            cmd.arg("--create");
        }
        let child = cmd.stdout(Stdio::piped()).stderr(Stdio::piped()).spawn()?;
        record(child.id());
        Ok(Self { child, port })
    }

    /// SIGTERM, not kill. Jobs are idempotent and `reclaim_stale` covers a
    /// hard kill, so this is about closing sqlite cleanly rather than about
    /// correctness.
    pub fn terminate(&mut self) {
        unsafe { libc::kill(self.child.id() as i32, libc::SIGTERM) };
        let _ = self.child.wait();
        forget();
    }
}

/// Poll `GET /api/config` until it answers.
///
/// That route is chosen because it already exists, is cheap, and needs no
/// library-specific state -- it answers as soon as the app is serving, which
/// is exactly the question being asked.
pub fn wait_healthy(port: u16, deadline: Duration) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/config");
    let until = Instant::now() + deadline;
    while Instant::now() < until {
        if ureq::get(&url)
            .timeout(Duration::from_millis(500))
            .call()
            .is_ok()
        {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

/// Where the running sidecar's identity is recorded, so a crashed app can
/// clean up after itself on the next launch.
fn pidfile() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_default();
    PathBuf::from(home).join("Library/Application Support/splitstep/sidecar.pid")
}

pub fn record(pid: u32) {
    let path = pidfile();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, pid.to_string());
}

pub fn forget() {
    let _ = std::fs::remove_file(pidfile());
}

/// Kill a sidecar left behind by a previous run.
///
/// The single-instance plugin guards the APP, not the server, so it does not
/// cover this: force-quit or crash the shell and its Python child keeps
/// running, holding library.db. The next launch would then spawn a second
/// worker against the same database -- exactly the enqueue race
/// jobs/handlers.py names, and the one thing the single-instance plugin was
/// supposed to prevent.
///
/// The pid is checked against the process name before anything is signalled:
/// pids are recycled, and SIGTERM to whatever happens to hold that number now
/// would be far worse than an orphan.
pub fn reap_orphan() {
    let Ok(text) = std::fs::read_to_string(pidfile()) else {
        return;
    };
    let Ok(pid) = text.trim().parse::<i32>() else {
        forget();
        return;
    };
    if !is_our_server(pid) {
        forget();
        return;
    }
    unsafe { libc::kill(pid, libc::SIGTERM) };
    // Give it a moment to close sqlite before the new one opens it.
    for _ in 0..20 {
        std::thread::sleep(Duration::from_millis(100));
        if !is_our_server(pid) {
            break;
        }
    }
    forget();
}

fn is_our_server(pid: i32) -> bool {
    let Ok(out) = Command::new("/bin/ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
    else {
        return false;
    };
    String::from_utf8_lossy(&out.stdout).contains("splitstep-server")
}

/// Where the frozen server lives, bundle-first with a dev fallback -- the
/// same shape `splitstep/resources.py` uses on the Python side, for the same
/// reason: the dev loop must not need a bundle to exist.
pub fn server_exe(resource_dir: Option<&Path>) -> PathBuf {
    if let Some(dir) = resource_dir {
        let bundled = dir.join("splitstep-server").join("splitstep-server");
        if bundled.is_file() {
            return bundled;
        }
    }
    PathBuf::from("../packaging/dist/splitstep-server/splitstep-server")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn free_port_returns_a_bindable_port() {
        // Retried, because the gap between free_port() dropping its listener
        // and us re-binding is precisely the race the doc comment describes
        // -- and it is not theoretical: this test flaked against a sibling
        // test doing the same thing in a parallel thread. That is why
        // open_library retries on a fresh port rather than trusting one.
        // What is being asserted is that the port is usable, not that the
        // race never happens.
        let mut last = None;
        for _ in 0..5 {
            let port = free_port().expect("a port");
            assert!(port > 1024, "should not hand back a privileged port");
            match TcpListener::bind(("127.0.0.1", port)) {
                Ok(_) => return,
                Err(e) => last = Some(e),
            }
        }
        panic!("no free port was bindable in five attempts: {last:?}");
    }

    #[test]
    fn free_port_does_not_repeat_itself_immediately() {
        let a = free_port().unwrap();
        let b = free_port().unwrap();
        assert_ne!(a, b, "two calls in a row must not collide");
    }

    #[test]
    fn wait_healthy_gives_up_rather_than_hanging() {
        let port = free_port().unwrap();
        let start = Instant::now();
        assert!(!wait_healthy(port, Duration::from_millis(600)));
        assert!(start.elapsed() < Duration::from_secs(3), "must not hang");
    }

    #[test]
    fn is_our_server_rejects_a_recycled_pid() {
        // pid 1 is launchd. Signalling it because a pidfile went stale would
        // be far worse than leaving an orphan, so the name check is the part
        // that must never be wrong.
        assert!(!is_our_server(1));
    }

    #[test]
    fn is_our_server_is_false_for_a_dead_pid() {
        assert!(!is_our_server(999_999));
    }

    #[test]
    fn reap_orphan_tolerates_a_missing_or_garbage_pidfile() {
        // Must not panic on a first-ever launch, nor on a truncated file.
        forget();
        reap_orphan();
        record(0);
        reap_orphan();
        assert!(!pidfile().exists(), "a handled pidfile is always removed");
    }

    #[test]
    fn server_exe_falls_back_when_there_is_no_bundle() {
        let path = server_exe(None);
        assert!(path.to_string_lossy().contains("packaging/dist"));
    }

    #[test]
    fn server_exe_prefers_a_real_bundled_binary() {
        let dir = std::env::temp_dir().join("splitstep-server-exe-test");
        let nested = dir.join("splitstep-server");
        std::fs::create_dir_all(&nested).unwrap();
        let exe = nested.join("splitstep-server");
        std::fs::write(&exe, b"#!/bin/sh\n").unwrap();
        assert_eq!(server_exe(Some(&dir)), exe);
        // A resource dir that exists but holds no server must still fall
        // back rather than returning a path to nothing.
        let empty = std::env::temp_dir().join("splitstep-empty-resources");
        std::fs::create_dir_all(&empty).unwrap();
        assert!(server_exe(Some(&empty)).to_string_lossy().contains("packaging/dist"));
    }
}
