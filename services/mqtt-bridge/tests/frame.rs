use mqtt_bridge::frame::*;
use mqtt_bridge::intervention::*;

fn sample_header() -> HilHeader {
    HilHeader {
        mode: 1,
        node_id: 0xFFFF_0001,
        seq: 42,
        ts_us: 123_456_789_012,
        sample_rate_hz: 10_000,
        n_samples: 1000,
    }
}

#[test]
fn build_then_parse_roundtrip() {
    let h = sample_header();
    let samples: Vec<i16> = (0..1000).map(|i| ((i as i32 * 37) % 65536 - 32768) as i16).collect();
    let buf = build_frame(&h, &samples);
    assert_eq!(buf.len(), 2032, "100 ms @ 10 kHz frame is 2032 bytes");
    assert_eq!(&buf[0..4], b"HILR");
    assert_eq!(buf[4], 1);
    assert_eq!(buf[5], 1);
    assert_eq!(&buf[6..8], &[0, 0]);
    assert_eq!(&buf[30..32], &[0, 0]);

    let (h2, s2) = parse_frame(&buf).expect("valid frame");
    assert_eq!(h2, h);
    assert_eq!(s2, samples);
}

#[test]
fn header_field_offsets_are_little_endian() {
    let h = sample_header();
    let buf = build_frame(&h, &[0i16; 1000]);
    assert_eq!(&buf[8..12], &0xFFFF_0001u32.to_le_bytes());
    assert_eq!(&buf[12..16], &42u32.to_le_bytes());
    assert_eq!(&buf[16..24], &123_456_789_012u64.to_le_bytes());
    assert_eq!(&buf[24..28], &10_000u32.to_le_bytes());
    assert_eq!(&buf[28..30], &1000u16.to_le_bytes());
}

#[test]
fn rejects_bad_frames() {
    let h = sample_header();
    let good = build_frame(&h, &[0i16; 4]);

    assert_eq!(parse_header(&good[..10]), Err(FrameError::TooShort(10)));

    let mut bad_magic = good.clone();
    bad_magic[0] = b'X';
    assert!(matches!(parse_header(&bad_magic), Err(FrameError::BadMagic(_))));

    let mut bad_ver = good.clone();
    bad_ver[4] = 2;
    assert_eq!(parse_header(&bad_ver), Err(FrameError::BadVersion(2)));

    let mut truncated = good.clone();
    truncated.pop();
    assert_eq!(
        parse_header(&truncated),
        Err(FrameError::LengthMismatch { expected: 40, actual: 39 })
    );
}

#[test]
fn node_hex_and_topics() {
    assert_eq!(node_hex(0xFFFF_0000), "ffff0000");
    assert_eq!(node_hex(80125), "000138fd");
    assert!(is_hil_node(0xFFFF_0001));
    assert!(!is_hil_node(99_999));
    assert_eq!(node_hex_from_topic("hil/ffff0000/raw"), Some("ffff0000"));
    assert_eq!(node_hex_from_topic("hil/ffff0001/status"), Some("ffff0001"));
    assert_eq!(node_hex_from_topic("hil/raw"), None);
    assert_eq!(node_hex_from_topic("swarm/ffff0000/raw"), None);
}

#[test]
fn intervention_to_cmd_mapping() {
    let json = r#"{"id":"u","t_ms":1,"kind":"toxin",
        "target":{"type":"node","node_id":4294901760},
        "params":{"intensity":0.8,"spread_ms":20000,"onset_ms":5000},"source":"dashboard"}"#;
    let iv: Intervention = serde_json::from_str(json).unwrap();
    assert_eq!(iv.target, Target::Node { node_id: 0xFFFF_0000 });
    assert_eq!(hil_targets(&iv, &[]), vec![0xFFFF_0000]);
    let cmd = to_cmd(&iv).unwrap();
    assert_eq!(cmd, HilCmd { mode: 1, intensity: 0.8 });
    assert_eq!(serde_json::to_string(&cmd).unwrap(), r#"{"mode":1,"intensity":0.8}"#);

    // region targets never reach HIL nodes; virtual node ids are ignored
    let region: Intervention = serde_json::from_str(
        r#"{"kind":"drug","target":{"type":"region","x":200,"y":125,"r":40}}"#,
    )
    .unwrap();
    assert!(hil_targets(&region, &[0xFFFF_0000]).is_empty());
    let virt: Intervention =
        serde_json::from_str(r#"{"kind":"drug","target":{"type":"node","node_id":5}}"#).unwrap();
    assert!(hil_targets(&virt, &[]).is_empty());

    // "all" -> both default boards plus any extra board seen on status
    let all: Intervention =
        serde_json::from_str(r#"{"kind":"radiation","target":{"type":"all"}}"#).unwrap();
    assert_eq!(hil_targets(&all, &[0xFFFF_0002, 7]), vec![0xFFFF_0000, 0xFFFF_0001, 0xFFFF_0002]);
    assert_eq!(to_cmd(&all).unwrap(), HilCmd { mode: 3, intensity: 1.0 });

    assert_eq!(kind_to_mode("baseline"), Some(0));
    assert_eq!(kind_to_mode("drug"), Some(2));
    assert_eq!(kind_to_mode("nope"), None);
}

#[test]
fn intervention_accepts_float_params() {
    // api-gateway serialises spread_ms / onset_ms as f64 and ids/t_ms as ints
    let json = r#"{"id":"f","t_ms":1757400000000,"kind":"toxin",
        "target":{"type":"node","node_id":4294901761},
        "params":{"intensity":0.8,"spread_ms":20000.0,"onset_ms":5000.5},"source":"dashboard"}"#;
    let iv: Intervention = serde_json::from_str(json).expect("float params must parse");
    assert_eq!(iv.params.spread_ms_u64(), Some(20000));
    assert_eq!(iv.params.onset_ms_u64(), Some(5001));
    assert_eq!(to_cmd(&iv).unwrap(), HilCmd { mode: 1, intensity: 0.8 });

    // integer params still fine, and an integer intensity is accepted too
    let json_int = r#"{"kind":"drug","target":{"type":"all"},"params":{"intensity":1,"spread_ms":20000}}"#;
    let iv2: Intervention = serde_json::from_str(json_int).unwrap();
    assert_eq!(iv2.params.spread_ms_u64(), Some(20000));
    assert_eq!(to_cmd(&iv2).unwrap(), HilCmd { mode: 2, intensity: 1.0 });
}
