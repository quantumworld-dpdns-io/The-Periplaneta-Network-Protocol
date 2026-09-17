//! Binary frame codecs per `proto/WIRE.md` (little-endian). Only the frames
//! swarm-gen produces/consumes are implemented here.

/// SwarmBinFrame header size (WIRE §4.2).
pub const SWBN_HEADER: usize = 32;
pub const SWBN_MAGIC: &[u8; 4] = b"SWBN";
pub const SWBN_VERSION: u8 = 1;

/// Encode a `SwarmBinFrame` into `out` (cleared first). `counts.len()` must fit u32.
pub fn encode_swarm_bin(
    out: &mut Vec<u8>,
    shard: u16,
    first_node_id: u32,
    bin_ms: u16,
    t_ms: u64,
    counts: &[u16],
) {
    out.clear();
    out.reserve(SWBN_HEADER + counts.len() * 2);
    out.extend_from_slice(SWBN_MAGIC); // 0..4
    out.push(SWBN_VERSION); // 4
    out.push(0); // 5 reserved
    out.extend_from_slice(&shard.to_le_bytes()); // 6..8
    out.extend_from_slice(&first_node_id.to_le_bytes()); // 8..12
    out.extend_from_slice(&(counts.len() as u32).to_le_bytes()); // 12..16
    out.extend_from_slice(&bin_ms.to_le_bytes()); // 16..18
    out.extend_from_slice(&[0u8; 2]); // 18..20 reserved
    out.extend_from_slice(&[0u8; 4]); // 20..24 reserved
    out.extend_from_slice(&t_ms.to_le_bytes()); // 24..32
    for c in counts {
        out.extend_from_slice(&c.to_le_bytes());
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(not(test), allow(dead_code))]
pub struct SwarmBin {
    pub shard: u16,
    pub first_node_id: u32,
    pub bin_ms: u16,
    pub t_ms: u64,
    pub counts: Vec<u16>,
}

/// Decode a `SwarmBinFrame` (used by tests and tooling).
#[cfg_attr(not(test), allow(dead_code))]
pub fn decode_swarm_bin(buf: &[u8]) -> Result<SwarmBin, String> {
    if buf.len() < SWBN_HEADER {
        return Err("short frame".into());
    }
    if &buf[0..4] != SWBN_MAGIC {
        return Err("bad magic".into());
    }
    if buf[4] != SWBN_VERSION {
        return Err(format!("bad version {}", buf[4]));
    }
    let shard = u16::from_le_bytes([buf[6], buf[7]]);
    let first_node_id = u32::from_le_bytes(buf[8..12].try_into().unwrap());
    let n = u32::from_le_bytes(buf[12..16].try_into().unwrap()) as usize;
    let bin_ms = u16::from_le_bytes([buf[16], buf[17]]);
    let t_ms = u64::from_le_bytes(buf[24..32].try_into().unwrap());
    if buf.len() < SWBN_HEADER + n * 2 {
        return Err("truncated counts".into());
    }
    let counts = buf[SWBN_HEADER..SWBN_HEADER + n * 2]
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .collect();
    Ok(SwarmBin { shard, first_node_id, bin_ms, t_ms, counts })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn swarm_bin_layout_matches_wire_md() {
        let counts: Vec<u16> = (0..1000u16).collect();
        let mut buf = Vec::new();
        encode_swarm_bin(&mut buf, 7, 7000, 100, 1_757_400_000_000, &counts);
        assert_eq!(buf.len(), 32 + 2000);
        assert_eq!(&buf[0..4], b"SWBN");
        assert_eq!(buf[4], 1);
        assert_eq!(buf[5], 0);
        assert_eq!(u16::from_le_bytes([buf[6], buf[7]]), 7);
        assert_eq!(u32::from_le_bytes(buf[8..12].try_into().unwrap()), 7000);
        assert_eq!(u32::from_le_bytes(buf[12..16].try_into().unwrap()), 1000);
        assert_eq!(u16::from_le_bytes([buf[16], buf[17]]), 100);
        assert_eq!(&buf[18..24], &[0u8; 6]);
        assert_eq!(u64::from_le_bytes(buf[24..32].try_into().unwrap()), 1_757_400_000_000);
        // counts[3] = 3 at offset 32 + 6
        assert_eq!(&buf[38..40], &3u16.to_le_bytes());
        // little-endian check on a multi-byte count
        assert_eq!(&buf[32 + 2 * 999..32 + 2 * 1000], &[0xE7, 0x03]);
    }

    #[test]
    fn swarm_bin_roundtrip() {
        let counts: Vec<u16> = vec![0, 1, 2, 65535, 42];
        let mut buf = Vec::new();
        encode_swarm_bin(&mut buf, 65535, 0xFFFF0000, 50, 12345, &counts);
        let d = decode_swarm_bin(&buf).unwrap();
        assert_eq!(d, SwarmBin { shard: 65535, first_node_id: 0xFFFF0000, bin_ms: 50, t_ms: 12345, counts });
        assert!(decode_swarm_bin(&buf[..31]).is_err());
        let mut bad = buf.clone();
        bad[0] = b'X';
        assert!(decode_swarm_bin(&bad).is_err());
    }
}
