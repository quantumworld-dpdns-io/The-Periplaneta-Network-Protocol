//! `HilRawFrame` codec -- proto/WIRE.md section 4.1. Little-endian, 32-byte header.

use std::fmt;

pub const MAGIC: [u8; 4] = *b"HILR";
pub const VERSION: u8 = 1;
pub const HEADER_LEN: usize = 32;
/// Lowest HIL node id (`0xFFFF0000 + hil_index`).
pub const HIL_BASE: u32 = 0xFFFF_0000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HilHeader {
    pub mode: u8,
    pub node_id: u32,
    pub seq: u32,
    pub ts_us: u64,
    pub sample_rate_hz: u32,
    pub n_samples: u16,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FrameError {
    TooShort(usize),
    BadMagic([u8; 4]),
    BadVersion(u8),
    /// Payload length does not equal `32 + 2 * n_samples`.
    LengthMismatch { expected: usize, actual: usize },
}

impl fmt::Display for FrameError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FrameError::TooShort(n) => write!(f, "frame too short: {n} bytes"),
            FrameError::BadMagic(m) => write!(f, "bad magic {:?}", m),
            FrameError::BadVersion(v) => write!(f, "unsupported version {v}"),
            FrameError::LengthMismatch { expected, actual } => {
                write!(f, "length mismatch: expected {expected}, got {actual}")
            }
        }
    }
}
impl std::error::Error for FrameError {}

/// Parse and validate the 32-byte header. Also checks that the buffer holds
/// exactly `n_samples` i16 samples after the header.
pub fn parse_header(buf: &[u8]) -> Result<HilHeader, FrameError> {
    if buf.len() < HEADER_LEN {
        return Err(FrameError::TooShort(buf.len()));
    }
    let magic: [u8; 4] = buf[0..4].try_into().unwrap();
    if magic != MAGIC {
        return Err(FrameError::BadMagic(magic));
    }
    if buf[4] != VERSION {
        return Err(FrameError::BadVersion(buf[4]));
    }
    let h = HilHeader {
        mode: buf[5],
        node_id: u32::from_le_bytes(buf[8..12].try_into().unwrap()),
        seq: u32::from_le_bytes(buf[12..16].try_into().unwrap()),
        ts_us: u64::from_le_bytes(buf[16..24].try_into().unwrap()),
        sample_rate_hz: u32::from_le_bytes(buf[24..28].try_into().unwrap()),
        n_samples: u16::from_le_bytes(buf[28..30].try_into().unwrap()),
    };
    let expected = HEADER_LEN + 2 * h.n_samples as usize;
    if buf.len() != expected {
        return Err(FrameError::LengthMismatch { expected, actual: buf.len() });
    }
    Ok(h)
}

/// Parse header and decode the sample block.
pub fn parse_frame(buf: &[u8]) -> Result<(HilHeader, Vec<i16>), FrameError> {
    let h = parse_header(buf)?;
    let samples = buf[HEADER_LEN..]
        .chunks_exact(2)
        .map(|c| i16::from_le_bytes([c[0], c[1]]))
        .collect();
    Ok((h, samples))
}

/// Serialise a frame. `n_samples` in the header is taken from `samples.len()`.
pub fn build_frame(h: &HilHeader, samples: &[i16]) -> Vec<u8> {
    let n = samples.len();
    assert!(n <= u16::MAX as usize, "too many samples for u16 n_samples");
    let mut out = Vec::with_capacity(HEADER_LEN + 2 * n);
    out.extend_from_slice(&MAGIC);
    out.push(VERSION);
    out.push(h.mode);
    out.extend_from_slice(&[0, 0]); // reserved
    out.extend_from_slice(&h.node_id.to_le_bytes());
    out.extend_from_slice(&h.seq.to_le_bytes());
    out.extend_from_slice(&h.ts_us.to_le_bytes());
    out.extend_from_slice(&h.sample_rate_hz.to_le_bytes());
    out.extend_from_slice(&(n as u16).to_le_bytes());
    out.extend_from_slice(&[0, 0]); // reserved
    for s in samples {
        out.extend_from_slice(&s.to_le_bytes());
    }
    out
}

/// 8 lowercase hex chars, e.g. `ffff0000` (WIRE section 1).
pub fn node_hex(node_id: u32) -> String {
    format!("{node_id:08x}")
}

pub fn is_hil_node(node_id: u32) -> bool {
    node_id >= HIL_BASE
}

/// Extract `{node_hex}` from `hil/{node_hex}/raw` or `hil/{node_hex}/status`.
pub fn node_hex_from_topic(topic: &str) -> Option<&str> {
    let mut it = topic.split('/');
    match (it.next(), it.next(), it.next(), it.next()) {
        (Some("hil"), Some(hex), Some(_), None) if hex.len() == 8 => Some(hex),
        _ => None,
    }
}
