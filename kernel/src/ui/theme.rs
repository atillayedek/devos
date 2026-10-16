//! DevUI design tokens — the single source of DevOS's visual language
//! (dark, calm, high contrast; one accent gradient; generous spacing; 16 px corner radius).

use super::Color;

pub const BG_TOP: Color = Color::rgb(0x0B1020);
pub const BG_BOTTOM: Color = Color::rgb(0x131B3A);
pub const SURFACE: Color = Color::rgb(0x111827);
pub const BORDER: Color = Color::rgb(0x263354);
pub const TEXT: Color = Color::rgb(0xE5E7EB);
pub const TEXT_MUTED: Color = Color::rgb(0x94A3B8);
pub const ACCENT: Color = Color::rgb(0x3B82F6);
pub const ACCENT_2: Color = Color::rgb(0x8B5CF6);
pub const SUCCESS: Color = Color::rgb(0x22C55E);
pub const WARNING: Color = Color::rgb(0xF59E0B);
pub const DANGER: Color = Color::rgb(0xEF4444);
pub const USER_OUTPUT: Color = Color::rgb(0x7DD3FC);

pub const RADIUS: i32 = 16;
pub const SPACE: i32 = 24;
