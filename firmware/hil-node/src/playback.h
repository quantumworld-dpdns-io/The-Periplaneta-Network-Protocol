// Playback engine for the HIL node: replays the spike-train template in
// include/template.h at 10 kHz and applies the intervention modes of
// proto/WIRE.md section 8:
//
//   baseline  : template as recorded; any residual pathology fades slowly
//   toxin     : severity ramps up to `intensity` over TOXIN_RAMP_S; spikes are
//               dropped with p = severity and amplitude scales 1 - 0.7*severity
//   drug      : severity ramps back toward 0 at a rate ~ intensity
//   radiation : RADIATION_BURST_S of high-rate extra spikes, then noisy
//               suppression (noise x(1+3*intensity), severity -> 0.6*intensity)
//
// The pure functions (step_pathology / compute_effects / next_mode) carry the
// semantics and are unit-tested natively (test/test_playback). `Playback`
// wraps them with an ISR-safe, integer-only sample generator.
#pragma once
#include <stdint.h>

#ifdef ARDUINO
#include <Arduino.h>   // IRAM_ATTR
#else
#ifndef IRAM_ATTR
#define IRAM_ATTR
#endif
#endif

namespace hil {

enum Mode : uint8_t { MODE_BASELINE = 0, MODE_TOXIN = 1, MODE_DRUG = 2, MODE_RADIATION = 3 };

constexpr float TOXIN_RAMP_S      = 20.0f;  // toxin: time to reach full `intensity`
constexpr float DRUG_RECOVERY_S   = 15.0f;  // drug @ intensity 1: time to clear severity 1
constexpr float BASELINE_FADE_S   = 60.0f;  // baseline: natural recovery of severity 1
constexpr float RADIATION_BURST_S = 2.0f;
constexpr float RADIATION_RAMP_S  = 5.0f;   // post-burst suppression ramp
constexpr float BUTTON_INTENSITY  = 0.8f;   // intensity used when the BOOT button cycles modes

/// Slowly varying state that persists across mode changes.
struct Pathology {
  float severity;     // 0 = healthy .. 1 = fully silenced
  float t_in_mode_s;  // time since the last set_mode()
};

/// Instantaneous playback parameters.
struct Effects {
  float drop_prob;      // probability that a template spike is skipped, 0..1
  float amp_gain;       // spike amplitude multiplier
  float noise_gain;     // pink-noise multiplier
  float extra_rate_hz;  // extra Poisson spikes (radiation burst)
};

Mode      next_mode(Mode m);
Pathology step_pathology(Pathology p, Mode mode, float intensity, float dt_s);
Effects   compute_effects(const Pathology& p, Mode mode, float intensity);

/// Fixed-point view of `Effects` consumed by the ISR.
struct EffectsQ {
  uint32_t drop_q16;        // drop if (rng & 0xFFFF) < drop_q16
  uint32_t amp_q8;          // amplitude gain, 256 = 1.0
  uint32_t noise_scale_q16; // pink sum -> 0.1 uV, includes noise_gain
  uint32_t extra_p_q32;     // per-sample probability of an extra spike, 2^32 = 1.0
};
EffectsQ quantize(const Effects& e, uint32_t sample_rate_hz, int32_t noise_rms_lsb);

class Playback {
 public:
  void begin(uint32_t seed = 0x9E3779B9u);
  /// Set mode + intensity (0..1). Called from loop() / MQTT cmd / button.
  void set_mode(Mode m, float intensity);
  /// Advance pathology by dt and refresh the ISR parameters. Call from loop().
  void update(float dt_s);
  /// One 10 kHz sample in 0.1 uV. ISR-safe: RAM-only, integer math.
  int16_t IRAM_ATTR next_sample();

  Mode      mode() const { return mode_; }
  float     intensity() const { return intensity_; }
  Pathology pathology() const { return path_; }
  Effects   effects() const { return eff_; }
  uint32_t  spikes_emitted() const { return spikes_emitted_; }
  uint32_t  spikes_dropped() const { return spikes_dropped_; }

 private:
  static constexpr int kSlots = 4;
  static constexpr int kPinkRows = 12;
  struct Slot { uint16_t pos; uint16_t amp_q7; bool active; };

  uint32_t IRAM_ATTR rng();
  int32_t  IRAM_ATTR pink();
  void     IRAM_ATTR start_spike(uint32_t amp_q7);

  // control-plane state (loop context)
  Mode      mode_ = MODE_BASELINE;
  float     intensity_ = 0.0f;
  Pathology path_{0.0f, 0.0f};
  Effects   eff_{0.0f, 1.0f, 1.0f, 0.0f};

  // ISR state (RAM only)
  volatile EffectsQ q_{};
  uint32_t pos_ = 0;
  uint32_t next_spike_ = 0;
  uint32_t rng_state_ = 1;
  Slot     slots_[kSlots] = {};
  int32_t  pink_rows_[kPinkRows] = {};
  int32_t  pink_sum_ = 0;
  uint32_t pink_ctr_ = 0;
  volatile uint32_t spikes_emitted_ = 0;
  volatile uint32_t spikes_dropped_ = 0;
};

}  // namespace hil
