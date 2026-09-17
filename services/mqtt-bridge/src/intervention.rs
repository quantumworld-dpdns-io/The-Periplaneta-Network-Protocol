//! Intervention JSON (WIRE section 5) -> HIL command (WIRE section 8).

use serde::{Deserialize, Serialize};

use crate::frame::{is_hil_node, HIL_BASE};

#[derive(Debug, Clone, Deserialize)]
pub struct Intervention {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub t_ms: u64,
    pub kind: String,
    pub target: Target,
    #[serde(default)]
    pub params: Params,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub subtype: Option<String>,
}

#[derive(Debug, Clone, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Target {
    Region { x: f64, y: f64, r: f64 },
    Node { node_id: u32 },
    All,
}

/// WIRE section 5: params are JSON numbers that may be int or float -> always
/// parse as f64 (api-gateway serialises `spread_ms`/`onset_ms` as f64).
#[derive(Debug, Clone, Deserialize)]
pub struct Params {
    #[serde(default = "default_intensity")]
    pub intensity: f64,
    #[serde(default)]
    pub spread_ms: Option<f64>,
    #[serde(default)]
    pub onset_ms: Option<f64>,
}
fn default_intensity() -> f64 {
    1.0
}
impl Default for Params {
    fn default() -> Self {
        Params { intensity: 1.0, spread_ms: None, onset_ms: None }
    }
}
impl Params {
    /// `spread_ms` rounded to whole milliseconds.
    pub fn spread_ms_u64(&self) -> Option<u64> {
        self.spread_ms.map(|v| v.max(0.0).round() as u64)
    }
    /// `onset_ms` rounded to whole milliseconds.
    pub fn onset_ms_u64(&self) -> Option<u64> {
        self.onset_ms.map(|v| v.max(0.0).round() as u64)
    }
}

/// Payload for `hil/{node_hex}/cmd`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HilCmd {
    pub mode: u8,
    pub intensity: f32,
}

/// WIRE section 2 enum mapping. Unknown kinds -> `None` (ignored).
pub fn kind_to_mode(kind: &str) -> Option<u8> {
    match kind {
        "baseline" => Some(0),
        "toxin" => Some(1),
        "drug" => Some(2),
        "radiation" => Some(3),
        _ => None,
    }
}

/// Which HIL node ids an intervention should be forwarded to.
/// `known` = node ids seen on `hil/+/status`; `all` always covers at least
/// the two default boards (`ffff0000`, `ffff0001`) so a cold bridge still
/// forwards. Region targets never hit HIL nodes (they are not in the grid).
pub fn hil_targets(iv: &Intervention, known: &[u32]) -> Vec<u32> {
    match &iv.target {
        Target::Node { node_id } if is_hil_node(*node_id) => vec![*node_id],
        Target::Node { .. } | Target::Region { .. } => Vec::new(),
        Target::All => {
            let mut v: Vec<u32> = vec![HIL_BASE, HIL_BASE + 1];
            for k in known {
                if is_hil_node(*k) && !v.contains(k) {
                    v.push(*k);
                }
            }
            v.sort_unstable();
            v
        }
    }
}

/// Build the command for an intervention, or `None` if `kind` is unknown.
pub fn to_cmd(iv: &Intervention) -> Option<HilCmd> {
    let mode = kind_to_mode(&iv.kind)?;
    Some(HilCmd { mode, intensity: iv.params.intensity.clamp(0.0, 1.0) as f32 })
}
