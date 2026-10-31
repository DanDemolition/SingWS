//! The bits of Python's `str` and `os.path` the scan depends on, reproduced exactly (posixpath on macOS).

pub type R<T> = Result<T, String>;

pub fn decline<T>(why: impl AsRef<str>) -> R<T> {
    Err(format!("decline: {}", why.as_ref()))
}

/// `str.isspace()` for one character. Rust's `char::is_whitespace` lacks U+001C..U+001F, which Python counts.
pub fn is_py_space(c: char) -> bool {
    matches!(c,
        '\t' | '\n' | '\x0b' | '\x0c' | '\r' | '\x1c' | '\x1d' | '\x1e' | '\x1f' | ' ' | '\u{85}' | '\u{a0}' | '\u{1680}'
        | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

/// `str.strip()`.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// `posixpath.normpath`.
pub fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    let mut initial = 0;
    if path.starts_with('/') {
        initial = 1;
        // POSIX: exactly two leading slashes are kept, three or more collapse to one.
        if path.starts_with("//") && !path.starts_with("///") {
            initial = 2;
        }
    }
    let mut comps: Vec<&str> = Vec::new();
    for comp in path.split('/') {
        if comp.is_empty() || comp == "." {
            continue;
        }
        if comp != ".." || (initial == 0 && comps.is_empty()) || comps.last() == Some(&"..") {
            comps.push(comp);
        } else if !comps.is_empty() {
            comps.pop();
        }
    }
    let joined = comps.join("/");
    let out = format!("{}{}", "/".repeat(initial), joined);
    if out.is_empty() { ".".to_string() } else { out }
}

/// `os.path.abspath` (relative paths join the working directory `cwd`).
pub fn abspath(path: &str, cwd: &str) -> String {
    if path.starts_with('/') {
        normpath(path)
    } else {
        normpath(&format!("{cwd}/{path}"))
    }
}

/// `os.path.expanduser`: `~` and `~/...` through `$HOME`. `~name` needs the password database in Python; declined here.
pub fn expanduser(path: &str) -> R<String> {
    if !path.starts_with('~') {
        return Ok(path.to_string());
    }
    let end = path.find('/').unwrap_or(path.len());
    if end != 1 {
        return decline("`~user` paths");
    }
    let Some(home) = std::env::var_os("HOME") else { return decline("HOME is not set") };
    let home = home.into_string().map_err(|_| "decline: HOME is not UTF-8".to_string())?;
    // posixpath: userhome = home.rstrip('/'); result = (userhome + path[i:]) or '/'
    let userhome = home.trim_end_matches('/');
    let rest = &path[end..];
    let r = format!("{userhome}{rest}");
    Ok(if r.is_empty() { "/".to_string() } else { r })
}

/// `os.path.realpath` (non-strict): symlinks are resolved, components that do not exist are kept. A symlink loop is declined.
pub fn realpath(path: &str, cwd: &str) -> R<String> {
    let mut pending: Vec<String> = abspath(path, cwd).split('/').rev().map(str::to_string).collect();
    let mut resolved = String::from("/");
    let mut hops = 0;
    while let Some(name) = pending.pop() {
        if name.is_empty() || name == "." {
            continue;
        }
        if name == ".." {
            match resolved.rfind('/') {
                Some(0) | None => resolved = "/".to_string(),
                Some(i) => resolved.truncate(i),
            }
            continue;
        }
        let candidate = if resolved == "/" { format!("/{name}") } else { format!("{resolved}/{name}") };
        match std::fs::symlink_metadata(&candidate) {
            Ok(m) if m.file_type().is_symlink() => {
                hops += 1;
                if hops > 40 {
                    return decline("symlink loop or very deep symlink chain");
                }
                let target = std::fs::read_link(&candidate).map_err(|e| format!("readlink: {e}"))?;
                let target = target.into_os_string().into_string().map_err(|_| "decline: non-UTF-8 symlink target".to_string())?;
                if target.starts_with('/') {
                    resolved = "/".to_string();
                }
                for part in target.split('/').rev() {
                    pending.push(part.to_string());
                }
            }
            // Not a link, or not statable (a missing tail): kept as is, like Python's non-strict mode.
            _ => resolved = candidate,
        }
    }
    Ok(resolved)
}

/// `os.path.splitext(name)[0]` for a base name that does not start with a dot.
pub fn stem_of(name: &str) -> &str {
    match name.rfind('.') {
        Some(i) if name[..i].chars().any(|c| c != '.') => &name[..i],
        _ => name,
    }
}

/// `f"{n:,}"`.
pub fn thousands(n: i64) -> String {
    let digits = n.unsigned_abs().to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(ch);
    }
    if n < 0 { format!("-{out}") } else { out }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strip_knows_the_file_separator_characters() {
        assert_eq!(py_strip("\u{1c} a \u{1f}"), "a");
        assert_eq!(py_strip("\u{200b}a"), "\u{200b}a"); // zero-width space is not whitespace in either language
        assert_eq!(py_strip("\u{3000}a\u{a0}"), "a");
    }

    #[test]
    fn normpath_matches_posixpath() {
        assert_eq!(normpath("/a//b/./c/.."), "/a/b");
        assert_eq!(normpath("//a"), "//a");
        assert_eq!(normpath("///a"), "/a");
        assert_eq!(normpath("/.."), "/");
        assert_eq!(normpath("a/../.."), "..");
        assert_eq!(normpath(""), ".");
        assert_eq!(normpath("/"), "/");
    }

    #[test]
    fn splitext_ignores_leading_dots() {
        assert_eq!(stem_of("a.b.cdg"), "a.b");
        assert_eq!(stem_of("x.cdg"), "x");
        assert_eq!(stem_of(".cdg"), ".cdg");
        assert_eq!(stem_of("..x.cdg"), "..x");
        assert_eq!(stem_of("noext"), "noext");
    }

    #[test]
    fn thousands_groups() {
        assert_eq!(thousands(0), "0");
        assert_eq!(thousands(999), "999");
        assert_eq!(thousands(1000), "1,000");
        assert_eq!(thousands(1234567), "1,234,567");
    }
}
