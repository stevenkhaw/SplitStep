//! Draining the sidecar's output, so it cannot wedge, and keeping it.
//!
//! Piping a child's stdout without reading it is a deadlock with a delay
//! fuse. macOS gives a pipe 64KB -- measured here at 885 uvicorn access-log
//! lines -- and the write that does not fit blocks forever. The server then
//! stops answering HTTP mid-session, and SIGTERM cannot rescue it either:
//! uvicorn logs on its way out and blocks on the same full pipe, so the
//! shell's `wait` never returns. That is the whole content of the three
//! `.hang` reports this module exists to answer, all of them parked in
//! `wait4` with a reel open.
//!
//! So the pipes are read continuously, by a thread each, for the lifetime of
//! the process. Everything read is appended to one log file, because until
//! now the server's output had nowhere to go: `drain_stderr` read it once,
//! on a failed launch, and threw the rest away.

use std::collections::VecDeque;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

/// Enough to hold a Python traceback whole, so the startup-failure message
/// can be widened later without touching the reader.
const TAIL_LINES: usize = 40;

/// Rotate at 5MB. One reel session is a few hundred KB of access log, so
/// this keeps roughly the last week of use without ever being something a
/// user has to think about.
pub const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;

/// The last few lines the server printed, kept in memory.
///
/// Bounded on purpose: the log file is the archive, and this is only what a
/// failed launch needs to say out loud.
#[derive(Clone, Default)]
pub struct Tail {
    lines: Arc<Mutex<VecDeque<String>>>,
}

impl Tail {
    pub fn push(&self, line: String) {
        // Blank lines are dropped here rather than at the callsite because
        // the only consumer is a one-sentence error message, and uvicorn
        // pads its output.
        if line.trim().is_empty() {
            return;
        }
        let mut lines = self.lines.lock().unwrap();
        if lines.len() == TAIL_LINES {
            lines.pop_front();
        }
        lines.push_back(line);
    }

    /// The last `n` lines, oldest first.
    pub fn last(&self, n: usize) -> Vec<String> {
        let lines = self.lines.lock().unwrap();
        lines.iter().skip(lines.len().saturating_sub(n)).cloned().collect()
    }

}

/// Read `reader` to EOF, appending every line to `path` and to `tail`.
///
/// The file is optional and the loop is not: a log that cannot be opened is
/// an inconvenience, but a reader that stops reading is the deadlock. So a
/// failed open leaves `file` as None and the draining continues regardless.
pub fn drain<R>(reader: R, path: PathBuf, tail: Tail) -> JoinHandle<()>
where
    R: Read + Send + 'static,
{
    std::thread::spawn(move || {
        let mut file = open_append(&path);
        for line in BufReader::new(reader).lines() {
            let Ok(line) = line else { break };
            if let Some(file) = file.as_mut() {
                // O_APPEND, one write per line: two drains share this file
                // and neither may land inside the other's line.
                let _ = writeln!(file, "{line}");
            }
            tail.push(line);
        }
    })
}

fn open_append(path: &Path) -> Option<File> {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    OpenOptions::new().create(true).append(true).open(path).ok()
}

/// Move an oversized log aside, keeping exactly one previous run.
///
/// Called once per launch rather than per line: the size only matters at the
/// moment the file is opened, and checking it on every write would put a
/// stat syscall in the path that must never be slow.
pub fn rotate_if_large(path: &Path, max_bytes: u64) {
    let Ok(meta) = std::fs::metadata(path) else {
        return;
    };
    if meta.len() <= max_bytes {
        return;
    }
    let _ = std::fs::rename(path, path.with_extension("log.1"));
}

/// `~/Library/Logs/SplitStep/server.log` -- where Console.app looks, and
/// where a friend can be told to find it without being told what a path is.
pub fn default_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_default();
    PathBuf::from(home).join("Library/Logs/SplitStep/server.log")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tail_keeps_the_last_lines_and_drops_the_blank_ones() {
        let tail = Tail::default();
        tail.push("one".into());
        tail.push("".into());
        tail.push("two".into());
        tail.push("   ".into());
        tail.push("three".into());
        tail.push("four".into());
        // Chronological. The caller reverses when it wants newest-first.
        assert_eq!(tail.last(3), vec!["two", "three", "four"]);
    }

    #[test]
    fn tail_is_bounded_so_a_long_run_cannot_grow_it() {
        let tail = Tail::default();
        for i in 0..10_000 {
            tail.push(format!("line {i}"));
        }
        let kept = tail.last(TAIL_LINES + 100).len();
        assert!(kept <= TAIL_LINES, "tail grew to {kept}");
        assert_eq!(tail.last(1), vec!["line 9999"]);
    }

    #[test]
    fn drain_writes_every_line_to_the_log_and_to_the_tail() {
        let dir = std::env::temp_dir().join("splitstep-drain-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.log");

        let tail = Tail::default();
        let reader = std::io::Cursor::new(b"first\nsecond\n".to_vec());
        drain(reader, path.clone(), tail.clone()).join().unwrap();

        let written = std::fs::read_to_string(&path).unwrap();
        assert!(written.contains("first"), "log holds the line: {written:?}");
        assert!(written.contains("second"), "log holds the line: {written:?}");
        assert_eq!(tail.last(2), vec!["first", "second"]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rotate_moves_an_oversized_log_aside_rather_than_growing_forever() {
        let dir = std::env::temp_dir().join("splitstep-rotate-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.log");
        std::fs::write(&path, "old run\n").unwrap();

        rotate_if_large(&path, 4);

        assert!(!path.exists(), "the oversized log is moved out of the way");
        let previous = std::fs::read_to_string(path.with_extension("log.1")).unwrap();
        assert_eq!(previous, "old run\n", "the previous run is still readable");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rotate_leaves_a_small_log_alone() {
        let dir = std::env::temp_dir().join("splitstep-rotate-small-test");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("server.log");
        std::fs::write(&path, "short\n").unwrap();

        rotate_if_large(&path, 1024);

        assert_eq!(std::fs::read_to_string(&path).unwrap(), "short\n");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_default_log_lives_where_console_app_looks_for_it() {
        let path = default_path();
        let shown = path.to_string_lossy();
        assert!(shown.ends_with("Library/Logs/SplitStep/server.log"), "{shown}");
    }
}
