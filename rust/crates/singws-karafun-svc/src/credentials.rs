//! W4: the saved KaraFun login in the macOS Keychain, compatible with `karafun_credentials.py` (the oracle).
//!
//! Storage format (kept exactly): service `SERVICE`, account = the email (trimmed), data = the ASCII text `h:<hex of the password's UTF-8 bytes>`
//! (`security ... -w` prints non-ASCII data as hex, which would be ambiguous, hence the `h:` hex form). Rules kept from the Python: an email with `"` `\` CR or LF is
//! refused, an empty email or password is refused, `save` replaces THIS account only (never another saved login), `delete_all` removes every account of the service
//! (at most 5 passes, like the Python), `load` answers `None` for a missing, foreign-format or empty value.
//! The pure rules (`encode`, `decode`, `clean_email`) never touch the Keychain. The password is never logged or returned in an error.
//! Two backends: `keychain` (the Security framework: native, what the Python-free app will use) and `tool` (drives `/usr/bin/security` exactly like the Python).
//! FINDING (measured 2026-11-12): an item is readable without a prompt only by the program that created it. A login saved by the Python app (through `security`) is NOT
//! readable by the Security framework from another program (errAuthFailed -25293 with prompts disabled, a dialog otherwise), and an item the framework creates makes
//! `security -w` hang on a dialog. So while Python and Rust coexist use `tool`; the native backend needs a one-time approval (or a re-save) to take over an old login.
//! Note for a future driver: an item created by `/usr/bin/security` trusts that tool; a different program reading it may make macOS ask the user once.

/// The real service name of the operator's saved login. Tests and tools must NEVER use it (a test run once wiped the real login).
pub const SERVICE: &str = "SingWS KaraFun account";

/// Python `str.strip()` whitespace (adds the four information separators to Unicode White_Space).
fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// The email as the Python would use it, or `None` when it is refused.
pub fn clean_email(email: &str) -> Option<String> {
    let e = email.trim_matches(is_py_space);
    if e.is_empty() || e.chars().any(|c| matches!(c, '"' | '\\' | '\n' | '\r')) {
        return None;
    }
    Some(e.to_string())
}

/// The stored data for a password (`h:<hex>`), or `None` when the password is empty (refused).
pub fn encode(password: &str) -> Option<String> {
    if password.is_empty() {
        return None;
    }
    let mut s = String::from("h:");
    for b in password.as_bytes() {
        s.push_str(&format!("{b:02x}"));
    }
    Some(s)
}

/// The password from stored data, `None` for anything that is not `h:<hex of UTF-8>` or decodes to nothing.
pub fn decode(stored: &[u8]) -> Option<String> {
    // `security -w` prints data holding any non-printable byte (control characters, non-ASCII) as hex, which is never "h:...": measured against the real tool.
    if stored.iter().any(|&b| b < 0x20 || b >= 0x7f) {
        return None;
    }
    let text = std::str::from_utf8(stored).ok()?;
    let hex = text.strip_prefix("h:")?;
    // Python `bytes.fromhex`: ASCII whitespace is skipped between bytes, every byte is exactly two hex digits.
    let mut bytes: Vec<u8> = Vec::new();
    let raw: Vec<u8> = hex.bytes().collect();
    let mut i = 0;
    while i < raw.len() {
        if matches!(raw[i], b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c) {
            i += 1;
            continue;
        }
        if i + 1 >= raw.len() || !raw[i].is_ascii_hexdigit() || !raw[i + 1].is_ascii_hexdigit() {
            return None;
        }
        bytes.push(u8::from_str_radix(&hex[i..i + 2], 16).ok()?);
        i += 2;
    }
    let pw = String::from_utf8(bytes).ok()?;
    if pw.is_empty() {
        None
    } else {
        Some(pw)
    }
}

#[cfg(target_os = "macos")]
pub mod keychain {
    use super::*;
    use security_framework::passwords::{delete_generic_password, get_generic_password, set_generic_password};

    /// Same behaviour as `karafun_credentials.save`: replaces THIS account only.
    pub fn save(service: &str, email: &str, password: &str) -> bool {
        let (Some(email), Some(data)) = (clean_email(email), encode(password)) else { return false };
        let _ = delete_generic_password(service, &email);
        set_generic_password(service, &email, data.as_bytes()).is_ok()
    }

    pub fn load(service: &str, email: &str) -> Option<String> {
        let email = clean_email(email)?;
        decode(&get_generic_password(service, &email).ok()?)
    }

    /// One account. (The Python's `delete(email)` does not validate the email.)
    pub fn delete(service: &str, email: &str) {
        let _ = delete_generic_password(service, email.trim_matches(is_py_space));
    }
}

/// The `/usr/bin/security` backend: byte-for-byte what `karafun_credentials.py` does (same commands, same stored data, same replace/delete rules).
pub mod tool {
    use super::*;
    use std::io::Write;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    fn run(args: &[&str], stdin: Option<&str>, timeout: Duration) -> Option<(i32, Vec<u8>)> {
        let mut child = Command::new("/usr/bin/security").args(args).stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() }).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
        if let (Some(text), Some(mut pipe)) = (stdin, child.stdin.take()) {
            let _ = pipe.write_all(text.as_bytes());
        }
        let start = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if start.elapsed() < timeout => std::thread::sleep(Duration::from_millis(5)),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
            }
        }
        let out = child.wait_with_output().ok()?;
        Some((out.status.code().unwrap_or(-1), out.stdout))
    }

    pub fn save(service: &str, email: &str, password: &str) -> bool {
        let (Some(email), Some(data)) = (clean_email(email), encode(password)) else { return false };
        delete(service, &email);
        let hexed: String = data.bytes().map(|b| format!("{b:02x}")).collect();
        let cmd = format!("add-generic-password -a \"{email}\" -s \"{service}\" -X {hexed}\n");
        match run(&["-i"], Some(&cmd), Duration::from_secs(10)) {
            Some((0, out)) => !String::from_utf8_lossy(&out).to_lowercase().contains("error"),
            _ => false,
        }
    }

    pub fn load(service: &str, email: &str) -> Option<String> {
        let email = clean_email(email)?;
        let (code, out) = run(&["find-generic-password", "-a", &email, "-s", service, "-w"], None, Duration::from_secs(10))?;
        if code != 0 {
            return None;
        }
        let text = String::from_utf8(out).ok()?;
        decode(text.trim_end_matches('\n').as_bytes())
    }

    /// One account.
    pub fn delete(service: &str, email: &str) {
        let _ = run(&["delete-generic-password", "-s", service, "-a", email.trim_matches(is_py_space)], None, Duration::from_secs(10));
    }

    /// Every account of the service (at most 5 passes, like the Python).
    pub fn delete_all(service: &str) {
        for _ in 0..5 {
            match run(&["delete-generic-password", "-s", service], None, Duration::from_secs(10)) {
                Some((0, _)) => {}
                _ => return,
            }
        }
    }
}
