//! Spawning and health-checking the frozen Python server.

use std::io;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::logs::{self, Tail};

/// How long a SIGTERM is given before SIGKILL.
///
/// A ceiling, not a sleep: the wait ends the moment the process does. Five
/// seconds is chosen against uvicorn's graceful shutdown closing sqlite,
/// which takes milliseconds when it is able to run at all.
pub const TERM_GRACE: Duration = Duration::from_secs(5);

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
    tail: Tail,
    drains: Vec<JoinHandle<()>>,
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
        Ok(Self::from_child(child, port, logs::default_path()))
    }

    /// Take ownership of an already-spawned child and start reading it.
    ///
    /// Separate from `spawn` so the draining can be tested against a child
    /// that is easy to make chatty, rather than against a frozen Python
    /// build that has to exist first.
    pub fn from_child(mut child: Child, port: u16, log: PathBuf) -> Self {
        logs::rotate_if_large(&log, logs::MAX_LOG_BYTES);
        let tail = Tail::default();
        // Both streams into one file: interleaved is how they were read
        // before, and a reader chasing a failure wants them in one order.
        let mut drains = Vec::new();
        if let Some(out) = child.stdout.take() {
            drains.push(logs::drain(out, log.clone(), tail.clone()));
        }
        if let Some(err) = child.stderr.take() {
            drains.push(logs::drain(err, log, tail.clone()));
        }
        Self { child, port, tail, drains }
    }

    /// SIGTERM, then SIGKILL if that is ignored. Jobs are idempotent and
    /// `reclaim_stale` covers a hard kill, so the graceful half is about
    /// closing sqlite cleanly rather than about correctness -- and the
    /// bounded half is about quitting at all. An unbounded `wait()` here is
    /// what turned a wedged sidecar into an 18-second, then a 101-second,
    /// beachball on quit.
    pub fn terminate(&mut self) {
        unsafe { libc::kill(self.child.id() as i32, libc::SIGTERM) };
        let deadline = Instant::now() + TERM_GRACE;
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                forget();
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
        forget();
    }

    /// The last `n` lines the server printed, newest first -- the sentence a
    /// failed launch shows the user.
    ///
    /// Waits for the readers to reach EOF first, because the caller has just
    /// terminated the child and the lines that explain why may still be in
    /// flight. Bounded, like everything else that waits here: a stray
    /// grandchild holding the pipe open must not cost more than the message.
    pub fn last_output(&mut self, n: usize) -> String {
        let drains = std::mem::take(&mut self.drains);
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for drain in drains {
                let _ = drain.join();
            }
            let _ = tx.send(());
        });
        let _ = rx.recv_timeout(Duration::from_secs(2));

        let mut lines = self.tail.last(n);
        lines.reverse();
        lines.join(" ")
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
        for candidate in [
            // Where tauri.conf.json's resources MAP puts it. The map form
            // names the destination, so this is the layout we ship.
            dir.join("splitstep-server").join("splitstep-server"),
            // Where the resources LIST form puts it. Tauri sanitizes the
            // leading ".." of "../packaging/dist/splitstep-server" into an
            // "_up_" segment -- verified against a real bundle, where the
            // server landed at Contents/Resources/_up_/packaging/dist/.
            // Kept as a fallback because reverting the map to a list is a
            // one-character-looking edit that would otherwise produce an app
            // that builds, installs, launches, and never starts its server.
            dir.join("_up_")
                .join("packaging")
                .join("dist")
                .join("splitstep-server")
                .join("splitstep-server"),
        ] {
            if candidate.is_file() {
                return candidate;
            }
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
    fn server_exe_finds_the_up_layout_too() {
        // The list form of `resources` produces this, and an app that cannot
        // find its server launches to a window that never loads.
        let dir = std::env::temp_dir().join("splitstep-up-layout-test");
        let nested = dir.join("_up_/packaging/dist/splitstep-server");
        std::fs::create_dir_all(&nested).unwrap();
        let exe = nested.join("splitstep-server");
        std::fs::write(&exe, b"#!/bin/sh\n").unwrap();
        assert_eq!(server_exe(Some(&dir)), exe);
        let _ = std::fs::remove_dir_all(&dir);
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

    /// The bug this whole module was reshaped around.
    ///
    /// macOS gives a pipe 64KB, which is roughly 885 uvicorn access-log
    /// lines. With nobody reading, the 886th `write` blocks forever: the
    /// server stops answering HTTP, and SIGTERM cannot land either, because
    /// uvicorn's graceful shutdown logs on its way out and blocks on the
    /// same full pipe. Three `.hang` reports, all stuck in `wait4`.
    #[test]
    fn a_chatty_child_is_not_wedged_by_a_full_pipe() {
        let dir = std::env::temp_dir().join("splitstep-wedge-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();

        // ~180KB, comfortably past the 64KB a pipe holds.
        let child = Command::new("/bin/sh")
            .args([
                "-c",
                "i=0; while [ $i -lt 3000 ]; do \
                   echo \"INFO:     127.0.0.1:1 - PUT /api/reels/x/order HTTP/1.1 200 OK\"; \
                   i=$((i+1)); done",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("a shell");

        let mut sidecar = Sidecar::from_child(child, 0, dir.join("server.log"));

        let deadline = Instant::now() + Duration::from_secs(10);
        let mut status = None;
        while Instant::now() < deadline {
            if let Ok(Some(s)) = sidecar.child.try_wait() {
                status = Some(s);
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let status = status.expect("a drained child runs to completion instead of blocking");
        assert!(status.success(), "child exited with {status:?}");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn terminate_falls_back_to_sigkill_when_sigterm_is_ignored() {
        use std::os::unix::process::ExitStatusExt;

        // Exactly what a sidecar blocked mid-log looks like from out here: a
        // process that will not act on SIGTERM. Before the bounded wait,
        // `child.wait()` sat in wait4 until the user force-quit.
        let dir = std::env::temp_dir().join("splitstep-sigkill-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let child = Command::new("/bin/sh")
            .args(["-c", "trap '' TERM; echo ready; while true; do sleep 1; done"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("a shell");
        let mut sidecar = Sidecar::from_child(child, 0, dir.join("server.log"));

        // Wait for the trap to actually be installed. Signalling before the
        // shell has run `trap` kills it on the default disposition, and the
        // test then passes against the unbounded `wait()` this replaced --
        // which is what it did on first writing.
        let ready_by = Instant::now() + Duration::from_secs(10);
        while Instant::now() < ready_by && sidecar.tail.last(1) != vec!["ready".to_string()] {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(sidecar.tail.last(1), vec!["ready".to_string()], "the child never armed its trap");

        let start = Instant::now();
        sidecar.terminate();
        let elapsed = start.elapsed();

        assert!(
            elapsed >= TERM_GRACE,
            "SIGTERM was ignored here, so the grace period is the thing being spent: {elapsed:?}"
        );
        assert!(
            elapsed < TERM_GRACE + Duration::from_secs(3),
            "terminate must be bounded, took {elapsed:?}"
        );
        let status = sidecar.child.try_wait().expect("reaped").expect("reaped");
        assert_eq!(status.signal(), Some(libc::SIGKILL), "SIGTERM was ignored, so SIGKILL is what lands");

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The whole bug, against the real server rather than a stand-in.
    ///
    /// Ignored because it needs a Python that can `splitstep serve` and a
    /// library to open, neither of which a `cargo test` run is entitled to
    /// assume. Run it after touching anything in here:
    ///
    /// ```text
    /// SPLITSTEP_CLI=~/miniconda3/envs/splitstep/bin/splitstep \
    /// SPLITSTEP_TEST_LIBRARY=/tmp/ss-verify \
    ///   cargo test -- --ignored --nocapture
    /// ```
    #[test]
    #[ignore]
    fn the_real_server_survives_more_requests_than_a_pipe_holds() {
        use std::os::unix::process::ExitStatusExt;

        let (Ok(cli), Ok(library)) = (
            std::env::var("SPLITSTEP_CLI"),
            std::env::var("SPLITSTEP_TEST_LIBRARY"),
        ) else {
            eprintln!("skipped: set SPLITSTEP_CLI and SPLITSTEP_TEST_LIBRARY");
            return;
        };

        let port = free_port().unwrap();
        let child = Command::new(cli)
            .args(["--library", &library, "serve", "--port", &port.to_string()])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("the CLI");
        let log = std::env::temp_dir().join("splitstep-real-server-test/server.log");
        let _ = std::fs::remove_file(&log);
        let mut sidecar = Sidecar::from_child(child, port, log.clone());
        assert!(wait_healthy(port, Duration::from_secs(30)), "server never came up");

        // 1200 access-log lines, comfortably past the 885 a 64KB pipe holds.
        let url = format!("http://127.0.0.1:{port}/api/config");
        for i in 0..1200 {
            assert!(
                ureq::get(&url).timeout(Duration::from_secs(5)).call().is_ok(),
                "the server stopped answering after {i} requests"
            );
        }

        let start = Instant::now();
        sidecar.terminate();
        let elapsed = start.elapsed();
        let status = sidecar.child.try_wait().unwrap().unwrap();
        assert_eq!(
            status.signal(),
            Some(libc::SIGTERM),
            "a server that is not wedged shuts down on SIGTERM; SIGKILL means it was blocked"
        );
        assert!(elapsed < TERM_GRACE, "quit took {elapsed:?}");

        let written = std::fs::metadata(&log).map(|m| m.len()).unwrap_or(0);
        assert!(written > 65_536, "the log holds more than a pipe could: {written} bytes");
        eprintln!("served 1200 requests, quit in {elapsed:?}, log holds {written} bytes");
    }

    #[test]
    fn last_output_carries_the_lines_a_failed_launch_died_with() {
        // The message a friend sees when the server refuses to start. It
        // used to come from reading stderr to EOF once; now it comes from
        // the same drain that keeps the pipe empty, so there is one reader
        // rather than two disagreeing about who owns the stream.
        let dir = std::env::temp_dir().join("splitstep-last-output-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let child = Command::new("/bin/sh")
            .args([
                "-c",
                "echo INFO started 1>&2; echo '' 1>&2; \
                 echo 'sqlite3.OperationalError: unable to open database file' 1>&2; exit 1",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("a shell");
        let mut sidecar = Sidecar::from_child(child, 0, dir.join("server.log"));

        // Let it die of its own accord -- that is the case being described,
        // and signalling a shell that has not reached its first `echo` yet
        // measures the race rather than the message.
        let gone = Instant::now() + Duration::from_secs(5);
        while Instant::now() < gone && !matches!(sidecar.child.try_wait(), Ok(Some(_))) {
            std::thread::sleep(Duration::from_millis(20));
        }
        sidecar.terminate();

        // Newest first, blank lines dropped -- the shape `drain_stderr`
        // produced, kept so the dialog's sentence does not change.
        assert_eq!(
            sidecar.last_output(3),
            "sqlite3.OperationalError: unable to open database file INFO started"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn terminate_does_not_wait_out_the_grace_period_for_a_willing_child() {
        let child = Command::new("/bin/sh")
            .args(["-c", "while true; do sleep 1; done"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("a shell");
        let mut sidecar = Sidecar::from_child(child, 0, std::env::temp_dir().join("ignored.log"));

        let start = Instant::now();
        sidecar.terminate();

        assert!(
            start.elapsed() < Duration::from_secs(2),
            "SIGTERM is enough here; the grace period is a ceiling, not a sleep"
        );
    }
}
