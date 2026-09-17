// HilRawFrame -- proto/WIRE.md section 4.1 (little-endian, 32-byte header).
#pragma once
#include <stdint.h>

namespace hil {

constexpr uint32_t SAMPLE_RATE_HZ = 10000;
constexpr uint16_t FRAME_SAMPLES  = 1000;  // 100 ms
constexpr uint8_t  FRAME_VERSION  = 1;

struct __attribute__((packed)) RawHeader {
  char     magic[4];        // "HILR"
  uint8_t  version;         // 1
  uint8_t  mode;            // WIRE section 2
  uint16_t reserved0;
  uint32_t node_id;
  uint32_t seq;
  uint64_t ts_us;           // device micros since boot
  uint32_t sample_rate_hz;  // 10000
  uint16_t n_samples;       // 1000
  uint16_t reserved1;
};
static_assert(sizeof(RawHeader) == 32, "HilRawFrame header must be 32 bytes");

struct __attribute__((packed, aligned(8))) RawFrame {
  RawHeader hdr;
  int16_t   samples[FRAME_SAMPLES];
};
static_assert(sizeof(RawFrame) == 2032, "100 ms @ 10 kHz frame must be 2032 bytes");

}  // namespace hil
