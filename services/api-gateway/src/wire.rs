//! Binary frame codecs per `proto/WIRE.md` (little-endian) for the frames the
//! gateway consumes (FeatureFrame, HilRawFrame header) and emits (GridFrame).

pub const HIL_NODE_BASE: u32 = 0xFFFF_0000;

/// True for physical HIL node ids (`0xFFFF0000 + hil_index`).
pub fn is_hil(node_id: u32) -> bool {
    node_id >= HIL_NODE_BASE
}

/// `node_hex` text form: 8 lowercase hex chars.
pub fn node_hex(node_id: u32) -> String {
    format!("{node_id:08x}")
}

// ---------------------------------------------------------------------------
// FeatureFrame (WIRE §4.3)
// ---------------------------------------------------------------------------

pub const FEAT_HEADER: usize = 32;
pub const FEAT_RECORD: usize = 40;
pub const N_FEAT: usize = 8;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NodeRecord {
    pub stress: f32,
    pub state: u8,
    pub features: [f32; N_FEAT],
}

#[derive(Debug, Clone, PartialEq)]
pub struct FeatureFrame {
    pub shard: u16,
    pub first_node_id: u32,
    pub t_ms: u64,
    pub window_ms: u32,
    pub records: Vec<NodeRecord>,
}

impl FeatureFrame {
    pub fn node_id(&self, i: usize) -> u32 {
        self.first_node_id.wrapping_add(i as u32)
    }
}

pub fn decode_feature_frame(buf: &[u8]) -> Result<FeatureFrame, String> {
    if buf.len() < FEAT_HEADER {
        return Err("short FEAT frame".into());
    }
    if &buf[0..4] != b"FEAT" {
        return Err("bad FEAT magic".into());
    }
    if buf[4] != 1 {
        return Err(format!("bad FEAT version {}", buf[4]));
    }
    let n_feat = buf[5] as usize;
    if n_feat != N_FEAT {
        return Err(format!("unexpected n_feat {n_feat}"));
    }
    let shard = u16::from_le_bytes([buf[6], buf[7]]);
    let first_node_id = u32::from_le_bytes(buf[8..12].try_into().unwrap());
    let n_nodes = u32::from_le_bytes(buf[12..16].try_into().unwrap()) as usize;
    let t_ms = u64::from_le_bytes(buf[16..24].try_into().unwrap());
    let window_ms = u32::from_le_bytes(buf[24..28].try_into().unwrap());
    if buf.len() < FEAT_HEADER + n_nodes * FEAT_RECORD {
        return Err(format!("truncated FEAT frame: {} bytes for {n_nodes} nodes", buf.len()));
    }
    let mut records = Vec::with_capacity(n_nodes);
    for rec in buf[FEAT_HEADER..FEAT_HEADER + n_nodes * FEAT_RECORD].chunks_exact(FEAT_RECORD) {
        let stress = f32::from_le_bytes(rec[0..4].try_into().unwrap());
        let state = rec[4];
        let mut features = [0f32; N_FEAT];
        for (k, f) in features.iter_mut().enumerate() {
            let o = 8 + 4 * k;
            *f = f32::from_le_bytes(rec[o..o + 4].try_into().unwrap());
        }
        records.push(NodeRecord { stress, state, features });
    }
    Ok(FeatureFrame { shard, first_node_id, t_ms, window_ms, records })
}

/// Encoder used by tests (and handy for local synthetic feeds).
#[cfg_attr(not(test), allow(dead_code))]
pub fn encode_feature_frame(f: &FeatureFrame) -> Vec<u8> {
    let mut out = Vec::with_capacity(FEAT_HEADER + f.records.len() * FEAT_RECORD);
    out.extend_from_slice(b"FEAT");
    out.push(1);
    out.push(N_FEAT as u8);
    out.extend_from_slice(&f.shard.to_le_bytes());
    out.extend_from_slice(&f.first_node_id.to_le_bytes());
    out.extend_from_slice(&(f.records.len() as u32).to_le_bytes());
    out.extend_from_slice(&f.t_ms.to_le_bytes());
    out.extend_from_slice(&f.window_ms.to_le_bytes());
    out.extend_from_slice(&[0u8; 4]);
    for r in &f.records {
        out.extend_from_slice(&r.stress.to_le_bytes());
        out.push(r.state);
        out.extend_from_slice(&[0u8; 3]);
        for v in r.features {
            out.extend_from_slice(&v.to_le_bytes());
        }
    }
    out
}

// ---------------------------------------------------------------------------
// HilRawFrame (WIRE §4.1) — header only; payload is passed through verbatim.
// ---------------------------------------------------------------------------

pub const HILR_HEADER: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HilRawHeader {
    pub mode: u8,
    pub node_id: u32,
    pub seq: u32,
    pub ts_us: u64,
    pub sample_rate_hz: u32,
    pub n_samples: u16,
}

pub fn decode_hil_header(buf: &[u8]) -> Result<HilRawHeader, String> {
    if buf.len() < HILR_HEADER {
        return Err("short HILR frame".into());
    }
    if &buf[0..4] != b"HILR" {
        return Err("bad HILR magic".into());
    }
    if buf[4] != 1 {
        return Err(format!("bad HILR version {}", buf[4]));
    }
    let h = HilRawHeader {
        mode: buf[5],
        node_id: u32::from_le_bytes(buf[8..12].try_into().unwrap()),
        seq: u32::from_le_bytes(buf[12..16].try_into().unwrap()),
        ts_us: u64::from_le_bytes(buf[16..24].try_into().unwrap()),
        sample_rate_hz: u32::from_le_bytes(buf[24..28].try_into().unwrap()),
        n_samples: u16::from_le_bytes([buf[28], buf[29]]),
    };
    if buf.len() < HILR_HEADER + 2 * h.n_samples as usize {
        return Err("truncated HILR samples".into());
    }
    Ok(h)
}

// ---------------------------------------------------------------------------
// GridFrame (WIRE §4.4)
// ---------------------------------------------------------------------------

pub const GRID_HEADER: usize = 24;

/// Encode a GridFrame. `stress.len()` must equal `width*height` (row-major).
pub fn encode_grid_frame(width: u16, height: u16, t_ms: u64, stress: &[u8]) -> Vec<u8> {
    debug_assert_eq!(stress.len(), width as usize * height as usize);
    let mut out = Vec::with_capacity(GRID_HEADER + stress.len());
    out.extend_from_slice(b"GRID"); // 0..4
    out.push(1); // 4 version
    out.push(0); // 5 reserved
    out.extend_from_slice(&width.to_le_bytes()); // 6..8
    out.extend_from_slice(&height.to_le_bytes()); // 8..10
    out.extend_from_slice(&[0u8; 6]); // 10..16 reserved
    out.extend_from_slice(&t_ms.to_le_bytes()); // 16..24
    out.extend_from_slice(stress); // 24..
    out
}

/// Map stress in [0,1] to u8 0..255 (saturating, NaN -> 0).
#[inline]
pub fn stress_to_u8(s: f32) -> u8 {
    if s.is_nan() {
        0
    } else {
        (s.clamp(0.0, 1.0) * 255.0).round() as u8
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grid_frame_layout_matches_wire_md() {
        let (w, h) = (400u16, 250u16);
        let mut stress = vec![0u8; w as usize * h as usize];
        // node (x=5, y=2) -> index y*w+x
        stress[2 * 400 + 5] = 200;
        let buf = encode_grid_frame(w, h, 1_757_400_000_000, &stress);
        assert_eq!(buf.len(), 24 + 100_000);
        assert_eq!(&buf[0..4], b"GRID");
        assert_eq!(buf[4], 1);
        assert_eq!(buf[5], 0);
        assert_eq!(u16::from_le_bytes([buf[6], buf[7]]), 400);
        assert_eq!(u16::from_le_bytes([buf[8], buf[9]]), 250);
        assert_eq!(&buf[10..16], &[0u8; 6]);
        assert_eq!(u64::from_le_bytes(buf[16..24].try_into().unwrap()), 1_757_400_000_000);
        assert_eq!(buf[24 + 2 * 400 + 5], 200);
        assert_eq!(buf[24], 0);
    }

    #[test]
    fn stress_quantisation() {
        assert_eq!(stress_to_u8(0.0), 0);
        assert_eq!(stress_to_u8(1.0), 255);
        assert_eq!(stress_to_u8(1.7), 255);
        assert_eq!(stress_to_u8(-3.0), 0);
        assert_eq!(stress_to_u8(f32::NAN), 0);
        assert_eq!(stress_to_u8(0.5), 128);
    }

    #[test]
    fn feature_frame_roundtrip_and_offsets() {
        let f = FeatureFrame {
            shard: 3,
            first_node_id: 3000,
            t_ms: 1_757_400_001_000,
            window_ms: 1000,
            records: vec![
                NodeRecord { stress: 0.25, state: 1, features: [12.0, 83.3, 0.9, 0.1, 1.2, -0.5, 0.0, 0.0] },
                NodeRecord { stress: 1.0, state: 5, features: [0.0; 8] },
            ],
        };
        let buf = encode_feature_frame(&f);
        assert_eq!(buf.len(), 32 + 2 * 40);
        assert_eq!(&buf[0..4], b"FEAT");
        assert_eq!(buf[4], 1);
        assert_eq!(buf[5], 8);
        assert_eq!(u16::from_le_bytes([buf[6], buf[7]]), 3);
        assert_eq!(u32::from_le_bytes(buf[8..12].try_into().unwrap()), 3000);
        assert_eq!(u32::from_le_bytes(buf[12..16].try_into().unwrap()), 2);
        assert_eq!(u64::from_le_bytes(buf[16..24].try_into().unwrap()), 1_757_400_001_000);
        assert_eq!(u32::from_le_bytes(buf[24..28].try_into().unwrap()), 1000);
        // record 0: stress @32, state @36, features[0] @40, features[1] @44
        assert_eq!(f32::from_le_bytes(buf[32..36].try_into().unwrap()), 0.25);
        assert_eq!(buf[36], 1);
        assert_eq!(f32::from_le_bytes(buf[40..44].try_into().unwrap()), 12.0);
        assert_eq!(f32::from_le_bytes(buf[44..48].try_into().unwrap()), 83.3);
        // record 1 starts at 72
        assert_eq!(buf[72 + 4], 5);
        let d = decode_feature_frame(&buf).unwrap();
        assert_eq!(d, f);
        assert_eq!(d.node_id(1), 3001);
        assert!(decode_feature_frame(&buf[..buf.len() - 1]).is_err());
    }

    #[test]
    fn hil_header_decodes() {
        let mut buf = Vec::new();
        buf.extend_from_slice(b"HILR");
        buf.push(1);
        buf.push(2); // mode drug
        buf.extend_from_slice(&[0, 0]);
        buf.extend_from_slice(&0xFFFF0001u32.to_le_bytes());
        buf.extend_from_slice(&77u32.to_le_bytes());
        buf.extend_from_slice(&123_456_789u64.to_le_bytes());
        buf.extend_from_slice(&10_000u32.to_le_bytes());
        buf.extend_from_slice(&3u16.to_le_bytes());
        buf.extend_from_slice(&[0, 0]);
        assert_eq!(buf.len(), 32);
        assert!(decode_hil_header(&buf).is_err()); // samples missing
        buf.extend_from_slice(&[1, 0, 2, 0, 3, 0]);
        let h = decode_hil_header(&buf).unwrap();
        assert_eq!(h, HilRawHeader { mode: 2, node_id: 0xFFFF0001, seq: 77, ts_us: 123_456_789, sample_rate_hz: 10_000, n_samples: 3 });
        assert!(is_hil(h.node_id));
        assert!(!is_hil(99_999));
        assert_eq!(node_hex(h.node_id), "ffff0001");
        assert_eq!(node_hex(80125), "000138fd");
    }
}
