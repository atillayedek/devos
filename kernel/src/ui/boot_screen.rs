//! The DevOS boot screen: logo, title, progress and a live system log card.

use alloc::collections::VecDeque;
use alloc::string::String;
use alloc::vec::Vec;

use super::theme::*;
use super::{abs, coverage, sqrt, Color, Rect, Screen, TextSize, Weight};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum LineKind {
    Info,
    Ok,
    Warn,
    #[allow(dead_code)] // used by upcoming fatal-error reporting
    Error,
    User,
}

pub struct BootScreen {
    pub screen: Screen,
    card: Rect,
    progress: Rect,
    footer_y: i32,
    lines: VecDeque<(LineKind, String)>,
    max_lines: usize,
    /// Pre-rendered background of the whole screen (gradient + glow).
    background: Vec<u32>,
    /// Pre-rendered empty card (background, surface, border, heading).
    card_base: Vec<u32>,
    pub progress_value: u32, // 0..=100
}

impl BootScreen {
    pub fn new(mut screen: Screen) -> BootScreen {
        let (w, h) = (screen.width, screen.height);
        draw_background(&mut screen);
        let background = screen.snapshot(Rect { x: 0, y: 0, w, h });
        let logo = (h / 9).clamp(72, 128);
        let logo_y = (h / 10).max(SPACE);
        draw_logo(&mut screen, w / 2 - logo / 2, logo_y, logo);
        let title_y = logo_y + logo + SPACE;
        let title = "DevOS";
        let tw = Screen::text_width(title, TextSize::Title, Weight::Bold);
        screen.text(w / 2 - tw / 2, title_y, title, TextSize::Title, Weight::Bold, TEXT);
        let sub = "Rust-first developer operating system";
        let sw = Screen::text_width(sub, TextSize::Large, Weight::Regular);
        let sub_y = title_y + TextSize::Title.line_height();
        screen.text(w / 2 - sw / 2, sub_y, sub, TextSize::Large, Weight::Regular, TEXT_MUTED);

        let card_w = (w - 2 * SPACE * 2).min(920);
        let progress = Rect { x: w / 2 - card_w / 2, y: sub_y + TextSize::Large.line_height() + SPACE, w: card_w, h: 6 };
        let card_y = progress.y + progress.h + SPACE;
        let footer_y = h - SPACE - TextSize::Body.line_height();
        let card = Rect { x: progress.x, y: card_y, w: card_w, h: (footer_y - SPACE - card_y).max(120) };
        let max_lines = ((card.h - 3 * SPACE) / TextSize::Body.line_height()).max(1) as usize;
        // render the empty card once
        screen.rounded_rect(card, RADIUS, SURFACE, 215);
        screen.rounded_outline(card, RADIUS, BORDER, 255);
        screen.text(card.x + SPACE, card.y + SPACE - 4, "System", TextSize::Large, Weight::Bold, TEXT);
        let card_base = screen.snapshot(card);
        let mut bs = BootScreen {
            screen, card, progress, footer_y, lines: VecDeque::new(), max_lines, progress_value: 0, background, card_base,
        };
        bs.draw_card();
        bs.draw_progress();
        bs.screen.present_all();
        bs
    }

    pub fn log(&mut self, kind: LineKind, text: &str) {
        for part in text.split('\n').filter(|p| !p.is_empty()) {
            if self.lines.len() == self.max_lines {
                self.lines.pop_front();
            }
            self.lines.push_back((kind, String::from(part)));
        }
        self.draw_card();
        let c = self.card;
        self.screen.present(c);
    }

    pub fn set_progress(&mut self, value: u32) {
        self.progress_value = value.min(100);
        self.draw_progress();
        let p = self.progress;
        self.screen.present(Rect { x: p.x, y: p.y - 1, w: p.w, h: p.h + 2 });
    }

    pub fn footer(&mut self, text: &str) {
        let r = Rect { x: 0, y: self.footer_y, w: self.screen.width, h: TextSize::Body.line_height() };
        self.restore_background(r);
        let tw = Screen::text_width(text, TextSize::Body, Weight::Regular);
        self.screen.text(self.screen.width / 2 - tw / 2, self.footer_y, text, TextSize::Body, Weight::Regular, TEXT_MUTED);
        self.screen.present(r);
    }

    fn restore_background(&mut self, r: Rect) {
        let full = Rect { x: 0, y: 0, w: self.screen.width, h: self.screen.height };
        self.screen.restore_from(r, full, &self.background);
    }

    fn draw_progress(&mut self) {
        let p = self.progress;
        self.restore_background(Rect { x: p.x, y: p.y - 1, w: p.w, h: p.h + 2 });
        self.screen.rounded_rect(p, p.h / 2, BORDER, 255);
        let filled = p.w * self.progress_value as i32 / 100;
        if filled > 0 {
            // accent gradient along the bar
            let bar = Rect { x: p.x, y: p.y, w: filled.max(p.h), h: p.h };
            for x in bar.x..bar.x + bar.w {
                let t = ((x - p.x) * 255 / p.w.max(1)) as u32;
                let c = ACCENT.lerp(ACCENT_2, t);
                for y in bar.y..bar.y + bar.h {
                    let r = p.h as f32 / 2.0;
                    let dx = if x < bar.x + p.h / 2 {
                        (bar.x as f32 + r) - (x as f32 + 0.5)
                    } else if x >= bar.x + bar.w - p.h / 2 {
                        (x as f32 + 0.5) - (bar.x + bar.w) as f32 + r
                    } else {
                        0.0
                    };
                    let dy = abs(y as f32 + 0.5 - (bar.y as f32 + r));
                    let d = if dx > 0.0 { sqrt(dx * dx + dy * dy) - r } else { dy - r };
                    self.screen.blend(x, y, c, coverage(d));
                }
            }
        }
    }

    fn draw_card(&mut self) {
        let c = self.card;
        self.screen.restore(c, &self.card_base);
        let pad = SPACE;
        let mut y = c.y + pad + TextSize::Large.line_height() + 8;
        for (kind, text) in self.lines.iter() {
            let (dot, color) = match kind {
                LineKind::Info => (ACCENT, TEXT),
                LineKind::Ok => (SUCCESS, TEXT),
                LineKind::Warn => (WARNING, TEXT),
                LineKind::Error => (DANGER, DANGER),
                LineKind::User => (USER_OUTPUT, USER_OUTPUT),
            };
            draw_dot(&mut self.screen, c.x + pad + 4, y + 10, 4, dot);
            let max_chars = ((c.w - 2 * pad - 20) / Screen::text_width("m", TextSize::Body, Weight::Regular)) as usize;
            let shown: String = text.chars().take(max_chars).collect();
            self.screen.text(c.x + pad + 20, y, &shown, TextSize::Body, Weight::Regular, color);
            y += TextSize::Body.line_height();
        }
    }
}

fn background_at(screen: &Screen, x: i32, y: i32) -> Color {
    let (w, h) = (screen.width.max(1), screen.height.max(1));
    let base = BG_TOP.lerp(BG_BOTTOM, (y * 255 / h) as u32);
    // soft accent glow in the upper right and lower left
    let glow = |cx: i32, cy: i32, radius: i32, col: Color, strength: u32| -> (Color, u32) {
        let dx = (x - cx) as i64;
        let dy = (y - cy) as i64;
        let d2 = (dx * dx + dy * dy) as u64;
        let r2 = (radius as i64 * radius as i64) as u64;
        if d2 >= r2 { (col, 0) } else { (col, (strength as u64 * (r2 - d2) / r2) as u32) }
    };
    let (c1, a1) = glow(w * 4 / 5, h / 5, w / 2, ACCENT, 46);
    let (c2, a2) = glow(w / 6, h * 9 / 10, w / 2, ACCENT_2, 40);
    c2.over(c1.over(base, a1), a2)
}

fn draw_background(screen: &mut Screen) {
    for y in 0..screen.height {
        for x in 0..screen.width {
            let c = background_at(screen, x, y);
            screen.put(x, y, c);
        }
    }
}

fn draw_dot(screen: &mut Screen, cx: i32, cy: i32, r: i32, c: Color) {
    for y in cy - r - 1..=cy + r + 1 {
        for x in cx - r - 1..=cx + r + 1 {
            let dx = x as f32 + 0.5 - cx as f32;
            let dy = y as f32 + 0.5 - cy as f32;
            screen.blend(x, y, c, coverage(sqrt(dx * dx + dy * dy) - r as f32));
        }
    }
}

/// Distance from p to segment ab.
fn seg_dist(px: f32, py: f32, ax: f32, ay: f32, bx: f32, by: f32) -> f32 {
    let (vx, vy, wx, wy) = (bx - ax, by - ay, px - ax, py - ay);
    let len2 = vx * vx + vy * vy;
    let mut t = if len2 > 0.0 { (wx * vx + wy * vy) / len2 } else { 0.0 };
    t = t.clamp(0.0, 1.0);
    let (dx, dy) = (px - (ax + t * vx), py - (ay + t * vy));
    sqrt(dx * dx + dy * dy)
}

/// The DevOS mark: a rounded square with the accent gradient and a white `</>`.
fn draw_logo(screen: &mut Screen, x0: i32, y0: i32, size: i32) {
    let s = size as f32;
    let rect = Rect { x: x0, y: y0, w: size, h: size };
    let radius = s * 0.23;
    let stroke = s * 0.07;
    // strokes in a 256-unit design space
    let segs: [(f32, f32, f32, f32); 5] = [
        (84.0, 92.0, 48.0, 128.0),
        (48.0, 128.0, 84.0, 164.0),
        (172.0, 92.0, 208.0, 128.0),
        (208.0, 128.0, 172.0, 164.0),
        (144.0, 72.0, 112.0, 184.0),
    ];
    for y in y0..y0 + size {
        for x in x0..x0 + size {
            let base = super::rounded_coverage(x, y, rect, radius);
            if base == 0 {
                continue;
            }
            let t = (((x - x0) + (y - y0)) * 255 / (2 * size)) as u32;
            let col = ACCENT.lerp(ACCENT_2, t);
            screen.blend(x, y, col, base);
            let u = (x - x0) as f32 + 0.5;
            let v = (y - y0) as f32 + 0.5;
            let mut d = f32::MAX;
            for &(ax, ay, bx, by) in &segs {
                let k = s / 256.0;
                let dd = seg_dist(u, v, ax * k, ay * k, bx * k, by * k);
                if dd < d {
                    d = dd;
                }
            }
            let a = coverage(d - stroke / 2.0) * base / 255;
            screen.blend(x, y, Color::rgb(0xFFFFFF), a);
        }
    }
}
