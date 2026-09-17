//! Intervention validation (WIRE §5). `POST /interventions` accepts the record
//! without `id`/`t_ms`; the server fills them and produces the full record.

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const KINDS: [&str; 4] = ["baseline", "toxin", "drug", "radiation"];
pub const SOURCES: [&str; 3] = ["dashboard", "button", "script"];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Target {
    Region { x: f64, y: f64, r: f64 },
    Node { node_id: u32 },
    All,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Params {
    pub intensity: f64,
    pub spread_ms: f64,
    pub onset_ms: f64,
}

impl Default for Params {
    fn default() -> Self {
        Params { intensity: 0.8, spread_ms: 20_000.0, onset_ms: 5_000.0 }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Intervention {
    pub id: String,
    pub t_ms: u64,
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subtype: Option<String>,
    pub target: Target,
    pub params: Params,
    pub source: String,
}

pub struct Limits {
    pub grid_w: u32,
    pub grid_h: u32,
    pub nodes: u32,
}

fn num(v: &Value, key: &str, default: Option<f64>) -> Result<f64, String> {
    match v.get(key) {
        None | Some(Value::Null) => default.ok_or_else(|| format!("missing `{key}`")),
        Some(x) => x.as_f64().filter(|f| f.is_finite()).ok_or_else(|| format!("`{key}` must be a finite number")),
    }
}

/// Validate a client-supplied JSON object and fill `id` (uuid v4) and `t_ms`.
/// Client-supplied `id`/`t_ms` are ignored (the server is authoritative).
pub fn validate(v: Value, lim: &Limits, now_ms: u64) -> Result<Intervention, String> {
    let Value::Object(o) = &v else { return Err("body must be a JSON object".into()) };
    let kind = o.get("kind").and_then(|k| k.as_str()).ok_or("missing `kind`")?.to_string();
    if !KINDS.contains(&kind.as_str()) {
        return Err(format!("`kind` must be one of {}", KINDS.join("|")));
    }
    let source = match o.get("source") {
        None | Some(Value::Null) => "dashboard".to_string(),
        Some(Value::String(s)) if SOURCES.contains(&s.as_str()) => s.clone(),
        _ => return Err(format!("`source` must be one of {}", SOURCES.join("|"))),
    };
    let t = o.get("target").ok_or("missing `target`")?;
    let ttype = t.get("type").and_then(|x| x.as_str()).ok_or("missing `target.type`")?;
    let target = match ttype {
        "region" => {
            let x = num(t, "x", None)?;
            let y = num(t, "y", None)?;
            let r = num(t, "r", None)?;
            if x < 0.0 || y < 0.0 || x >= lim.grid_w as f64 || y >= lim.grid_h as f64 {
                return Err(format!("region centre out of grid (0..{}, 0..{})", lim.grid_w, lim.grid_h));
            }
            if r <= 0.0 || r > (lim.grid_w.max(lim.grid_h) as f64) * 2.0 {
                return Err("`target.r` must be > 0 and within the grid scale".into());
            }
            Target::Region { x, y, r }
        }
        "node" => {
            let id = t.get("node_id").and_then(|x| x.as_u64()).ok_or("missing `target.node_id`")?;
            if id > u32::MAX as u64 {
                return Err("`target.node_id` out of range".into());
            }
            let id = id as u32;
            if !(id < lim.nodes || (0xFFFF_0000..0xFFFF_0000 + 16).contains(&id)) {
                return Err(format!("`target.node_id` {id} is neither a twin (< {}) nor a HIL id", lim.nodes));
            }
            Target::Node { node_id: id }
        }
        "all" => Target::All,
        other => return Err(format!("unknown target type `{other}`")),
    };
    let d = Params::default();
    let p = o.get("params").cloned().unwrap_or(Value::Object(Default::default()));
    if !p.is_object() {
        return Err("`params` must be an object".into());
    }
    let intensity = num(&p, "intensity", Some(d.intensity))?;
    if !(0.0..=1.0).contains(&intensity) {
        return Err("`params.intensity` must be within [0,1]".into());
    }
    let spread_ms = num(&p, "spread_ms", Some(d.spread_ms))?;
    let onset_ms = num(&p, "onset_ms", Some(d.onset_ms))?;
    if spread_ms < 0.0 || spread_ms > 3_600_000.0 || onset_ms < 0.0 || onset_ms > 3_600_000.0 {
        return Err("`params.spread_ms`/`onset_ms` must be within [0, 3600000]".into());
    }
    let subtype = match o.get("subtype") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if is_subtype(s) => Some(s.clone()),
        _ => return Err("`subtype` must be 1–40 chars of [a-z0-9_-]".into()),
    };
    Ok(Intervention {
        id: uuid::Uuid::new_v4().to_string(),
        t_ms: now_ms,
        kind,
        subtype,
        target,
        params: Params { intensity, spread_ms, onset_ms },
        source,
    })
}

fn is_subtype(s: &str) -> bool {
    (1..=40).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn lim() -> Limits {
        Limits { grid_w: 400, grid_h: 250, nodes: 100_000 }
    }

    #[test]
    fn accepts_wire_example_and_fills_id_t() {
        let v = json!({ "kind": "toxin", "target": { "type": "region", "x": 200, "y": 125, "r": 40 },
                        "params": { "intensity": 0.8, "spread_ms": 20000, "onset_ms": 5000 }, "source": "dashboard" });
        let r = validate(v, &lim(), 1_757_400_000_000).unwrap();
        assert_eq!(r.t_ms, 1_757_400_000_000);
        assert_eq!(uuid::Uuid::parse_str(&r.id).unwrap().get_version_num(), 4);
        assert_eq!(r.kind, "toxin");
        assert!(r.subtype.is_none());
        assert_eq!(r.target, Target::Region { x: 200.0, y: 125.0, r: 40.0 });
        assert_eq!(r.params, Params { intensity: 0.8, spread_ms: 20000.0, onset_ms: 5000.0 });
        // serialises to the WIRE §5 shape
        let s: Value = serde_json::to_value(&r).unwrap();
        assert_eq!(s["target"]["type"], "region");
        assert_eq!(s["source"], "dashboard");
        assert!(s["id"].is_string() && s["t_ms"].is_u64());
    }

    #[test]
    fn defaults_for_optional_fields() {
        let r = validate(json!({ "kind": "baseline", "target": { "type": "all" } }), &lim(), 1).unwrap();
        assert_eq!(r.source, "dashboard");
        assert_eq!(r.params, Params::default());
        assert_eq!(r.target, Target::All);
        let r = validate(json!({ "kind": "drug", "target": { "type": "node", "node_id": 4294901760u64 }, "source": "button" }), &lim(), 1).unwrap();
        assert_eq!(r.target, Target::Node { node_id: 0xFFFF0000 });
        let r = validate(json!({ "kind": "toxin", "subtype": "tetrodotoxin", "target": { "type": "all" } }), &lim(), 1).unwrap();
        assert_eq!(r.subtype.as_deref(), Some("tetrodotoxin"));
    }

    #[test]
    fn rejects_bad_input() {
        let bad = [
            json!({ "target": { "type": "all" } }),
            json!({ "kind": "laser", "target": { "type": "all" } }),
            json!({ "kind": "toxin" }),
            json!({ "kind": "toxin", "target": { "type": "blob" } }),
            json!({ "kind": "toxin", "target": { "type": "region", "x": 200, "y": 125 } }),
            json!({ "kind": "toxin", "target": { "type": "region", "x": 900, "y": 125, "r": 10 } }),
            json!({ "kind": "toxin", "target": { "type": "region", "x": 10, "y": 10, "r": 0 } }),
            json!({ "kind": "toxin", "target": { "type": "node", "node_id": 100000 } }),
            json!({ "kind": "toxin", "target": { "type": "all" }, "params": { "intensity": 1.5 } }),
            json!({ "kind": "toxin", "target": { "type": "all" }, "params": { "spread_ms": -1 } }),
            json!({ "kind": "toxin", "target": { "type": "all" }, "source": "hacker" }),
            json!({ "kind": "toxin", "subtype": "TTX", "target": { "type": "all" } }),
            json!([1, 2]),
        ];
        for b in bad {
            assert!(validate(b.clone(), &lim(), 1).is_err(), "should reject {b}");
        }
    }
}
