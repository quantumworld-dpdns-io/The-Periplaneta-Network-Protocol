//! mqtt-bridge library: pure, unit-testable pieces (wire frame codec,
//! intervention -> HIL command mapping). The binary in `main.rs` wires these
//! to rumqttc / rdkafka / axum.

pub mod frame;
pub mod intervention;
