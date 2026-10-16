//! Enforces docs/LANGUAGE-POLICY.md on the source tree.

use std::path::{Path, PathBuf};

use crate::root;

/// Directories that are not DevOS sources (build output, archived prototype, VCS).
const SKIP: &[&str] = &["target", ".git", "legacy", "node_modules", "out", "screens-tmp"];

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Lang {
    Rust,
    Asm,
    C,
    Cpp,
    Python,
    Shell,
    TypeScript,
    JavaScript,
    Other,
}

fn lang(path: &Path) -> Lang {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "rs" => Lang::Rust,
        "s" | "S" | "asm" | "nasm" => Lang::Asm,
        "c" | "h" => Lang::C,
        "cc" | "cpp" | "cxx" | "hpp" | "hh" => Lang::Cpp,
        "py" => Lang::Python,
        "sh" | "bash" => Lang::Shell,
        "ts" | "tsx" | "mts" => Lang::TypeScript,
        "js" | "mjs" | "cjs" | "jsx" => Lang::JavaScript,
        _ => Lang::Other,
    }
}

/// Which languages a top-level directory may contain.
fn allowed(top: &str, l: Lang) -> bool {
    use Lang::*;
    match (top, l) {
        (_, Other) => true,
        ("kernel" | "boot", Rust | Asm) => true,
        ("drivers" | "libs" | "userland" | "toolchain", Rust) => true,
        ("xtask", Rust) => true,
        ("scripts", Rust | Python | Shell) => true,
        ("ports", C | Cpp | Asm | Rust) => true,
        ("web", TypeScript | JavaScript) => true,
        ("tests", Rust | Python) => true,
        ("docs" | "branding" | "targets", _) => false,
        _ => false,
    }
}

fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for e in entries.flatten() {
        let p = e.path();
        let name = e.file_name().to_string_lossy().to_string();
        if p.is_dir() {
            if !SKIP.contains(&name.as_str()) {
                walk(&p, out);
            }
        } else {
            out.push(p);
        }
    }
}

pub fn run() -> Result<(), String> {
    let root = root();
    let mut files = Vec::new();
    walk(&root, &mut files);
    let mut violations = Vec::new();
    let mut counts = std::collections::BTreeMap::new();
    for f in &files {
        let rel = f.strip_prefix(&root).unwrap();
        let l = lang(f);
        if l == Lang::Other {
            continue;
        }
        *counts.entry(format!("{l:?}")).or_insert(0usize) += 1;
        let top = rel.components().next().map(|c| c.as_os_str().to_string_lossy().to_string()).unwrap_or_default();
        if rel.components().count() == 1 || !allowed(&top, l) {
            violations.push(format!("{} ({l:?} is not allowed in {}/)", rel.display(), top));
        }
        if top == "ports" && (l == Lang::C || l == Lang::Cpp) {
            let port = rel.components().nth(1).map(|c| c.as_os_str().to_owned()).unwrap_or_default();
            if !root.join("ports").join(port).join("PORT.md").exists() {
                violations.push(format!("{}: port without PORT.md", rel.display()));
            }
        }
        if l == Lang::Rust && (top == "kernel" || top == "boot" || top == "libs" || top == "userland") {
            check_unsafe_comments(f, rel, &mut violations);
        }
    }
    if !violations.is_empty() {
        return Err(format!("language policy violations:\n  {}", violations.join("\n  ")));
    }
    println!("xtask: language policy OK ({})",
             counts.iter().map(|(k, v)| format!("{k}: {v}")).collect::<Vec<_>>().join(", "));
    Ok(())
}

/// Every `unsafe {` block in system code needs a `// SAFETY:` comment just above it.
fn check_unsafe_comments(path: &Path, rel: &Path, out: &mut Vec<String>) {
    let Ok(src) = std::fs::read_to_string(path) else { return };
    let lines: Vec<&str> = src.lines().collect();
    for (i, line) in lines.iter().enumerate() {
        let t = line.trim_start();
        if t.starts_with("//") || !(t.contains("unsafe {") || t.contains("unsafe{")) || t.contains("unsafe impl") {
            continue;
        }
        let documented = lines[..i].iter().rev().take(4).any(|l| l.contains("SAFETY:"));
        if !documented {
            out.push(format!("{}:{}: unsafe block without a // SAFETY: comment", rel.display(), i + 1));
        }
    }
}
