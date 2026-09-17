// Host-side tests for the pure playback semantics:  pio test -e native
#include <math.h>
#include <unity.h>

#include "playback.h"
#include "template.h"

using namespace hil;

void setUp() {}
void tearDown() {}

static uint32_t run_seconds(Playback& p, float seconds) {
  const uint32_t before = p.spikes_emitted();
  const uint32_t n = (uint32_t)(seconds * TPL_RATE_HZ);
  for (uint32_t i = 0; i < n; i++) {
    if (i % (TPL_RATE_HZ / 10) == 0) p.update(0.1f);
    (void)p.next_sample();
  }
  return p.spikes_emitted() - before;
}

void test_next_mode_cycles() {
  TEST_ASSERT_EQUAL(MODE_TOXIN, next_mode(MODE_BASELINE));
  TEST_ASSERT_EQUAL(MODE_DRUG, next_mode(MODE_TOXIN));
  TEST_ASSERT_EQUAL(MODE_RADIATION, next_mode(MODE_DRUG));
  TEST_ASSERT_EQUAL(MODE_BASELINE, next_mode(MODE_RADIATION));
}

void test_toxin_ramps_to_intensity_and_holds() {
  Pathology p{0.f, 0.f};
  for (int i = 0; i < 100; i++) p = step_pathology(p, MODE_TOXIN, 0.8f, 0.1f);   // 10 s
  TEST_ASSERT_FLOAT_WITHIN(0.02f, 0.4f, p.severity);                            // half way
  for (int i = 0; i < 600; i++) p = step_pathology(p, MODE_TOXIN, 0.8f, 0.1f);   // +60 s
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.8f, p.severity);                           // saturates at intensity
  Effects e = compute_effects(p, MODE_TOXIN, 0.8f);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.8f, e.drop_prob);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.f - 0.7f * 0.8f, e.amp_gain);
  TEST_ASSERT_EQUAL_FLOAT(0.f, e.extra_rate_hz);
}

void test_drug_recovers() {
  Pathology p{0.8f, 0.f};
  for (int i = 0; i < 300; i++) p = step_pathology(p, MODE_DRUG, 1.0f, 0.1f);    // 30 s
  TEST_ASSERT_EQUAL_FLOAT(0.f, p.severity);
  Effects e = compute_effects(p, MODE_DRUG, 1.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.f, e.drop_prob);
  TEST_ASSERT_EQUAL_FLOAT(1.f, e.amp_gain);
}

void test_radiation_burst_then_suppression() {
  Pathology p{0.f, 0.f};
  p = step_pathology(p, MODE_RADIATION, 1.0f, 1.0f);   // t = 1 s: inside burst
  Effects burst = compute_effects(p, MODE_RADIATION, 1.0f);
  TEST_ASSERT_TRUE(burst.extra_rate_hz > 100.f);
  TEST_ASSERT_TRUE(burst.amp_gain > 1.f);
  TEST_ASSERT_EQUAL_FLOAT(0.f, burst.drop_prob);
  for (int i = 0; i < 100; i++) p = step_pathology(p, MODE_RADIATION, 1.0f, 0.1f);  // t = 11 s
  Effects after = compute_effects(p, MODE_RADIATION, 1.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.f, after.extra_rate_hz);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 4.f, after.noise_gain);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.6f, after.drop_prob);
}

void test_quantize() {
  Effects e{0.5f, 1.0f, 1.0f, 100.f};
  EffectsQ q = quantize(e, 10000, 45);
  TEST_ASSERT_EQUAL_UINT32(32768, q.drop_q16);
  TEST_ASSERT_EQUAL_UINT32(256, q.amp_q8);
  TEST_ASSERT_UINT32_WITHIN(1, (uint32_t)(0.01 * 4294967296.0), q.extra_p_q32);
  TEST_ASSERT_TRUE(q.noise_scale_q16 > 0);
}

void test_baseline_replays_all_template_spikes() {
  Playback p;
  p.begin(12345);
  uint32_t n = run_seconds(p, 10.0f);   // one full loop
  TEST_ASSERT_EQUAL_UINT32(TPL_N_SPIKES, n);
  TEST_ASSERT_EQUAL_UINT32(0, p.spikes_dropped());
}

void test_baseline_noise_level() {
  Playback p;
  p.begin(777);
  // measure RMS over 1 s while ignoring spike windows is fiddly; instead
  // check the signal is non-trivial and bounded.
  double acc = 0; int32_t mx = 0;
  for (int i = 0; i < 10000; i++) {
    int32_t s = p.next_sample();
    acc += (double)s * s;
    if (s > mx) mx = s;
    if (-s > mx) mx = -s;
  }
  double rms = sqrt(acc / 10000.0);
  TEST_ASSERT_TRUE(rms > 20.0 && rms < 400.0);   // ~45 LSB noise + spikes
  TEST_ASSERT_TRUE(mx >= 600);                    // at least one spike present
}

void test_full_toxin_silences() {
  Playback p;
  p.begin(99);
  p.set_mode(MODE_TOXIN, 1.0f);
  run_seconds(p, 30.0f);                    // ramp fully (20 s) and beyond
  uint32_t n = run_seconds(p, 10.0f);
  TEST_ASSERT_EQUAL_UINT32(0, n);
  TEST_ASSERT_TRUE(p.spikes_dropped() > 100);
}

void test_radiation_burst_adds_spikes() {
  Playback p;
  p.begin(5);
  p.set_mode(MODE_RADIATION, 1.0f);
  uint32_t n = run_seconds(p, 2.0f);        // burst window
  // template ~19 Hz + 150 Hz extra over 2 s -> ~340 spikes; allow generous margin
  TEST_ASSERT_TRUE(n > 150);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_next_mode_cycles);
  RUN_TEST(test_toxin_ramps_to_intensity_and_holds);
  RUN_TEST(test_drug_recovers);
  RUN_TEST(test_radiation_burst_then_suppression);
  RUN_TEST(test_quantize);
  RUN_TEST(test_baseline_replays_all_template_spikes);
  RUN_TEST(test_baseline_noise_level);
  RUN_TEST(test_full_toxin_silences);
  RUN_TEST(test_radiation_burst_adds_spikes);
  return UNITY_END();
}
