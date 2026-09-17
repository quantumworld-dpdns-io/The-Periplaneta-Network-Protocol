#include "playback.h"

#include <string.h>

#include "template.h"

namespace hil {

// Template copies in RAM: the 10 kHz ISR must never touch flash-resident
// .rodata (cache may be disabled during Wi-Fi/NVS flash writes).
static uint32_t s_spike_t[TPL_N_SPIKES];
static uint8_t  s_spike_amp[TPL_N_SPIKES];
static int16_t  s_wave[TPL_WAVE_N];

// ----------------------------------------------------------------------------
// Pure semantics
// ----------------------------------------------------------------------------
static inline float clamp01(float x) { return x < 0.f ? 0.f : (x > 1.f ? 1.f : x); }

Mode next_mode(Mode m) {
  switch (m) {
    case MODE_BASELINE:  return MODE_TOXIN;
    case MODE_TOXIN:     return MODE_DRUG;
    case MODE_DRUG:      return MODE_RADIATION;
    default:             return MODE_BASELINE;
  }
}

Pathology step_pathology(Pathology p, Mode mode, float intensity, float dt_s) {
  intensity = clamp01(intensity);
  p.t_in_mode_s += dt_s;
  switch (mode) {
    case MODE_TOXIN: {
      // progressive: ramp up toward `intensity`, never spontaneously improve
      const float target = intensity;
      if (p.severity < target) {
        p.severity += dt_s * intensity / TOXIN_RAMP_S;
        if (p.severity > target) p.severity = target;
      }
      break;
    }
    case MODE_DRUG:
      p.severity -= dt_s * intensity / DRUG_RECOVERY_S;
      break;
    case MODE_RADIATION: {
      if (p.t_in_mode_s > RADIATION_BURST_S) {
        const float target = 0.6f * intensity;
        if (p.severity < target) {
          p.severity += dt_s * target / RADIATION_RAMP_S;
          if (p.severity > target) p.severity = target;
        }
      }
      break;
    }
    case MODE_BASELINE:
    default:
      p.severity -= dt_s / BASELINE_FADE_S;
      break;
  }
  p.severity = clamp01(p.severity);
  return p;
}

Effects compute_effects(const Pathology& p, Mode mode, float intensity) {
  intensity = clamp01(intensity);
  Effects e;
  e.drop_prob     = p.severity;
  e.amp_gain      = 1.0f - 0.7f * p.severity;
  e.noise_gain    = 1.0f;
  e.extra_rate_hz = 0.0f;
  if (mode == MODE_RADIATION) {
    if (p.t_in_mode_s <= RADIATION_BURST_S) {
      // burst: hyper-excitation, no dropping, bigger spikes, somewhat noisier
      e.drop_prob     = 0.0f;
      e.amp_gain      = 1.0f + 0.3f * intensity;
      e.extra_rate_hz = 150.0f * intensity;
      e.noise_gain    = 1.0f + intensity;
    } else {
      e.noise_gain = 1.0f + 3.0f * intensity;
    }
  }
  return e;
}

EffectsQ quantize(const Effects& e, uint32_t sample_rate_hz, int32_t noise_rms_lsb) {
  EffectsQ q;
  q.drop_q16 = (uint32_t)(clamp01(e.drop_prob) * 65536.0f);
  float amp = e.amp_gain < 0.f ? 0.f : (e.amp_gain > 8.f ? 8.f : e.amp_gain);
  q.amp_q8 = (uint32_t)(amp * 256.0f + 0.5f);
  // Voss-McCartney sum of (kPinkRows + 1) uniform int16 values has an RMS of
  // sqrt(rows+1) * 32768 / sqrt(3). Scale it to noise_rms_lsb * noise_gain.
  const float rows_rms = 18918.6f * 3.6055f;  // sqrt(13) rows
  float ng = e.noise_gain < 0.f ? 0.f : (e.noise_gain > 16.f ? 16.f : e.noise_gain);
  q.noise_scale_q16 = (uint32_t)((float)noise_rms_lsb * ng / rows_rms * 65536.0f + 0.5f);
  float p = e.extra_rate_hz <= 0.f ? 0.f : e.extra_rate_hz / (float)sample_rate_hz;
  if (p > 0.5f) p = 0.5f;
  q.extra_p_q32 = (uint32_t)(p * 4294967296.0f);
  return q;
}

// ----------------------------------------------------------------------------
// Playback
// ----------------------------------------------------------------------------
void Playback::begin(uint32_t seed) {
  memcpy(s_spike_t, TPL_SPIKE_T, sizeof(s_spike_t));
  memcpy(s_spike_amp, TPL_SPIKE_AMP, sizeof(s_spike_amp));
  memcpy(s_wave, TPL_WAVE, sizeof(s_wave));
  rng_state_ = seed ? seed : 1u;
  pos_ = 0;
  next_spike_ = 0;
  for (auto& s : slots_) s.active = false;
  for (auto& r : pink_rows_) r = 0;
  pink_sum_ = 0;
  pink_ctr_ = 0;
  mode_ = MODE_BASELINE;
  intensity_ = 0.f;
  path_ = Pathology{0.f, 0.f};
  update(0.f);
}

void Playback::set_mode(Mode m, float intensity) {
  mode_ = m;
  intensity_ = clamp01(intensity);
  path_.t_in_mode_s = 0.f;
  update(0.f);
}

void Playback::update(float dt_s) {
  path_ = step_pathology(path_, mode_, intensity_, dt_s);
  eff_ = compute_effects(path_, mode_, intensity_);
  EffectsQ q = quantize(eff_, TPL_RATE_HZ, TPL_NOISE_RMS);
  // field-wise stores: each is a single aligned 32-bit write
  q_.drop_q16 = q.drop_q16;
  q_.amp_q8 = q.amp_q8;
  q_.noise_scale_q16 = q.noise_scale_q16;
  q_.extra_p_q32 = q.extra_p_q32;
}

uint32_t IRAM_ATTR Playback::rng() {
  uint32_t x = rng_state_;
  x ^= x << 13;
  x ^= x >> 17;
  x ^= x << 5;
  rng_state_ = x;
  return x;
}

// Voss-McCartney pink noise: row k is refreshed every 2^(k+1) samples.
int32_t IRAM_ATTR Playback::pink() {
  pink_ctr_++;
  uint32_t c = pink_ctr_;
  int k = 0;
  while (((c >> k) & 1u) == 0u && k < kPinkRows) k++;
  if (k < kPinkRows) {
    int32_t nv = (int16_t)(rng() & 0xFFFFu);
    pink_sum_ += nv - pink_rows_[k];
    pink_rows_[k] = nv;
  }
  int32_t white = (int16_t)(rng() & 0xFFFFu);
  return pink_sum_ + white;
}

void IRAM_ATTR Playback::start_spike(uint32_t amp_q7) {
  for (auto& s : slots_) {
    if (!s.active) {
      s.active = true;
      s.pos = 0;
      s.amp_q7 = (uint16_t)(amp_q7 > 0xFFFFu ? 0xFFFFu : amp_q7);
      spikes_emitted_ = spikes_emitted_ + 1;
      return;
    }
  }
  // all slots busy (extreme burst): drop silently
}

int16_t IRAM_ATTR Playback::next_sample() {
  // --- template spike scheduling ------------------------------------------
  if (next_spike_ < TPL_N_SPIKES && pos_ == s_spike_t[next_spike_]) {
    const uint32_t amp_base = s_spike_amp[next_spike_];  // 128 = 1.0
    next_spike_++;
    if ((rng() & 0xFFFFu) < q_.drop_q16) {
      spikes_dropped_ = spikes_dropped_ + 1;
    } else {
      start_spike((amp_base * q_.amp_q8) >> 8);
    }
  }
  // --- extra (burst) spikes -------------------------------------------------
  if (q_.extra_p_q32 && rng() < q_.extra_p_q32) {
    start_spike((128u * q_.amp_q8) >> 8);
  }
  // --- render ---------------------------------------------------------------
  int32_t acc = 0;
  for (auto& s : slots_) {
    if (!s.active) continue;
    acc += ((int32_t)s_wave[s.pos] * (int32_t)s.amp_q7) >> 7;
    if (++s.pos >= TPL_WAVE_N) s.active = false;
  }
  const int64_t n = (int64_t)pink() * (int64_t)q_.noise_scale_q16;
  acc += (int32_t)(n >> 16);

  if (++pos_ >= TPL_LEN) {
    pos_ = 0;
    next_spike_ = 0;
  }
  if (acc > 32767) acc = 32767;
  if (acc < -32768) acc = -32768;
  return (int16_t)acc;
}

}  // namespace hil
