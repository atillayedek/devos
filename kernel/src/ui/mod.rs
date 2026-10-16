//! Kernel graphics: a double-buffered framebuffer surface with anti-aliased primitives.
//! The colours and metrics follow the DevUI design tokens (see `theme`).

pub mod boot_screen;
pub mod theme;

use alloc::vec::Vec;

use devos_bootinfo::{Framebuffer, PixelFormat};
use noto_sans_mono_bitmap::{get_raster, get_raster_width, FontWeight, RasterHeight};

use crate::mm::phys_to_virt;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Color(pub u32); // 0xRRGGBB

impl Color {
    pub const fn rgb(hex: u32) -> Color {
        Color(hex & 0xFF_FFFF)
    }
    fn r(self) -> u32 {
        (self.0 >> 16) & 0xFF
    }
    fn g(self) -> u32 {
        (self.0 >> 8) & 0xFF
    }
    fn b(self) -> u32 {
        self.0 & 0xFF
    }
    /// Linear blend: `a` (0..=255) of `self` over `under`.
    pub fn over(self, under: Color, a: u32) -> Color {
        let mix = |x: u32, y: u32| (x * a + y * (255 - a)) / 255;
        Color((mix(self.r(), under.r()) << 16) | (mix(self.g(), under.g()) << 8) | mix(self.b(), under.b()))
    }
    pub fn lerp(self, other: Color, t_255: u32) -> Color {
        other.over(self, t_255)
    }
}

#[derive(Clone, Copy, Debug)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

pub struct Screen {
    pub width: i32,
    pub height: i32,
    stride: usize,
    format: PixelFormat,
    hw: *mut u32,
    back: Vec<u32>,
}

// SAFETY: the screen is only used behind the console lock.
unsafe impl Send for Screen {}

impl Screen {
    pub fn new(fb: &Framebuffer) -> Option<Screen> {
        if fb.format == PixelFormat::None || fb.width == 0 {
            return None;
        }
        Some(Screen {
            width: fb.width as i32,
            height: fb.height as i32,
            stride: fb.stride as usize,
            format: fb.format,
            hw: phys_to_virt(fb.phys_addr) as *mut u32,
            back: alloc::vec![0; (fb.width * fb.height) as usize],
        })
    }

    #[inline]
    pub fn get(&self, x: i32, y: i32) -> Color {
        Color(self.back[(y * self.width + x) as usize])
    }

    #[inline]
    pub fn put(&mut self, x: i32, y: i32, c: Color) {
        if x >= 0 && y >= 0 && x < self.width && y < self.height {
            self.back[(y * self.width + x) as usize] = c.0;
        }
    }

    #[inline]
    pub fn blend(&mut self, x: i32, y: i32, c: Color, a: u32) {
        if a == 0 || x < 0 || y < 0 || x >= self.width || y >= self.height {
            return;
        }
        let under = self.get(x, y);
        self.put(x, y, if a >= 255 { c } else { c.over(under, a) });
    }

    /// Copy of a rectangle of the back buffer (row-major, r.w * r.h pixels).
    pub fn snapshot(&self, r: Rect) -> Vec<u32> {
        let mut out = Vec::with_capacity((r.w * r.h) as usize);
        for y in r.y..r.y + r.h {
            for x in r.x..r.x + r.w {
                out.push(if x >= 0 && y >= 0 && x < self.width && y < self.height { self.get(x, y).0 } else { 0 });
            }
        }
        out
    }

    /// Writes a snapshot taken with `snapshot(r)` back to the same rectangle.
    pub fn restore(&mut self, r: Rect, pixels: &[u32]) {
        self.restore_from(r, r, pixels);
    }

    /// Restores `r` from `pixels`, a snapshot of the larger rectangle `src`.
    pub fn restore_from(&mut self, r: Rect, src: Rect, pixels: &[u32]) {
        for y in r.y.max(src.y).max(0)..(r.y + r.h).min(src.y + src.h).min(self.height) {
            for x in r.x.max(src.x).max(0)..(r.x + r.w).min(src.x + src.w).min(self.width) {
                let v = pixels[((y - src.y) * src.w + (x - src.x)) as usize];
                self.back[(y * self.width + x) as usize] = v;
            }
        }
    }

    /// Copies a rectangle of the back buffer to the visible framebuffer.
    pub fn present(&mut self, r: Rect) {
        let x0 = r.x.clamp(0, self.width) as usize;
        let x1 = (r.x + r.w).clamp(0, self.width) as usize;
        let y0 = r.y.clamp(0, self.height) as usize;
        let y1 = (r.y + r.h).clamp(0, self.height) as usize;
        for y in y0..y1 {
            for x in x0..x1 {
                let c = self.back[y * self.width as usize + x];
                let px = match self.format {
                    PixelFormat::Bgr => c, // memory order B,G,R,x == 0x00RRGGBB little-endian
                    _ => ((c & 0xFF) << 16) | (c & 0xFF00) | ((c >> 16) & 0xFF),
                };
                // SAFETY: (x, y) is inside the mode's visible area; stride >= width.
                unsafe { self.hw.add(y * self.stride + x).write_volatile(px) };
            }
        }
    }

    pub fn present_all(&mut self) {
        self.present(Rect { x: 0, y: 0, w: self.width, h: self.height });
    }

    pub fn fill(&mut self, r: Rect, c: Color) {
        for y in r.y.max(0)..(r.y + r.h).min(self.height) {
            for x in r.x.max(0)..(r.x + r.w).min(self.width) {
                self.put(x, y, c);
            }
        }
    }

    /// Anti-aliased rounded rectangle (`alpha` 0..=255 for translucency).
    pub fn rounded_rect(&mut self, r: Rect, radius: i32, c: Color, alpha: u32) {
        let rad = radius as f32;
        for y in r.y..r.y + r.h {
            for x in r.x..r.x + r.w {
                let cov = rounded_coverage(x, y, r, rad);
                if cov > 0 {
                    self.blend(x, y, c, cov * alpha / 255);
                }
            }
        }
    }

    /// One-pixel anti-aliased outline of a rounded rectangle.
    pub fn rounded_outline(&mut self, r: Rect, radius: i32, c: Color, alpha: u32) {
        let inner = Rect { x: r.x + 1, y: r.y + 1, w: r.w - 2, h: r.h - 2 };
        let band = radius + 2;
        for y in r.y..r.y + r.h {
            for x in r.x..r.x + r.w {
                // only the outer band can contain outline pixels
                if x >= r.x + band && x < r.x + r.w - band && y >= r.y + band && y < r.y + r.h - band {
                    continue;
                }
                let outer = rounded_coverage(x, y, r, radius as f32);
                let inn = rounded_coverage(x, y, inner, (radius - 1) as f32);
                let cov = outer.saturating_sub(inn);
                if cov > 0 {
                    self.blend(x, y, c, cov * alpha / 255);
                }
            }
        }
    }

    pub fn text(&mut self, x: i32, y: i32, s: &str, size: TextSize, weight: Weight, c: Color) -> i32 {
        let (h, w) = (size.raster(), weight.font());
        let adv = get_raster_width(w, h) as i32;
        let mut cx = x;
        for ch in s.chars() {
            let glyph = get_raster(ch, w, h).or_else(|| get_raster('?', w, h));
            if let Some(g) = glyph {
                for (gy, row) in g.raster().iter().enumerate() {
                    for (gx, &a) in row.iter().enumerate() {
                        self.blend(cx + gx as i32, y + gy as i32, c, a as u32);
                    }
                }
            }
            cx += adv;
        }
        cx - x
    }

    pub fn text_width(s: &str, size: TextSize, weight: Weight) -> i32 {
        get_raster_width(weight.font(), size.raster()) as i32 * s.chars().count() as i32
    }
}

#[derive(Clone, Copy)]
pub enum TextSize {
    Body,
    Large,
    Title,
}

impl TextSize {
    fn raster(self) -> RasterHeight {
        match self {
            TextSize::Body => RasterHeight::Size16,
            TextSize::Large => RasterHeight::Size20,
            TextSize::Title => RasterHeight::Size32,
        }
    }
    pub fn line_height(self) -> i32 {
        match self {
            TextSize::Body => 22,
            TextSize::Large => 28,
            TextSize::Title => 40,
        }
    }
}

#[derive(Clone, Copy)]
pub enum Weight {
    Regular,
    Bold,
}

impl Weight {
    fn font(self) -> FontWeight {
        match self {
            Weight::Regular => FontWeight::Regular,
            Weight::Bold => FontWeight::Bold,
        }
    }
}

/// Square root for the soft-float kernel (Newton iteration, enough for pixel coverage).
pub fn sqrt(v: f32) -> f32 {
    if v <= 0.0 {
        return 0.0;
    }
    let mut x = if v > 1.0 { v / 2.0 } else { 1.0 };
    for _ in 0..12 {
        x = 0.5 * (x + v / x);
    }
    x
}

/// Pixel coverage (0..=255) of (x, y) inside a rounded rectangle, using the signed distance.
fn rounded_coverage(x: i32, y: i32, r: Rect, rad: f32) -> u32 {
    // Fast paths without floating point: straight edges and the interior.
    let ri = rad as i32 + 1;
    let inside_x = x >= r.x + ri && x < r.x + r.w - ri;
    let inside_y = y >= r.y + ri && y < r.y + r.h - ri;
    if inside_x || inside_y {
        let in_rect = x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h;
        return if in_rect { 255 } else { 0 };
    }
    let px = x as f32 + 0.5;
    let py = y as f32 + 0.5;
    let cx = r.x as f32 + r.w as f32 / 2.0;
    let cy = r.y as f32 + r.h as f32 / 2.0;
    let hx = r.w as f32 / 2.0 - rad;
    let hy = r.h as f32 / 2.0 - rad;
    let qx = abs(px - cx) - hx;
    let qy = abs(py - cy) - hy;
    let ox = if qx > 0.0 { qx } else { 0.0 };
    let oy = if qy > 0.0 { qy } else { 0.0 };
    let inside = if qx > qy { qx } else { qy };
    let d = sqrt(ox * ox + oy * oy) + if inside < 0.0 { inside } else { 0.0 } - rad;
    coverage(d)
}

/// Coverage of a pixel whose centre is `d` pixels outside a shape edge.
pub fn coverage(d: f32) -> u32 {
    let c = 0.5 - d;
    if c <= 0.0 {
        0
    } else if c >= 1.0 {
        255
    } else {
        (c * 255.0) as u32
    }
}

pub fn abs(v: f32) -> f32 {
    if v < 0.0 { -v } else { v }
}
