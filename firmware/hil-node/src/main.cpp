// Cockroach Internet -- HIL node firmware (ESP32-S3-DevKitC-1)
//
// * 10 kHz hardware timer ISR renders the spike-train template into a double
//   buffer of 1000-sample (100 ms) HilRawFrames (proto/WIRE.md section 4.1).
// * loop() publishes each finished frame to  hil/{node_hex}/raw  (QoS 0),
//   a retained JSON status to  hil/{node_hex}/status  every 2 s, and applies
//   {"mode":u8,"intensity":f} commands from  hil/{node_hex}/cmd.
// * BOOT button (GPIO0) cycles baseline -> toxin -> drug -> radiation.
//
// Build flags (platformio.ini / secrets.ini): WIFI_SSID WIFI_PASS MQTT_HOST
// MQTT_PORT HIL_INDEX FW_VERSION.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_timer.h>

#include "hil_frame.h"
#include "playback.h"

#ifndef HIL_INDEX
#define HIL_INDEX 0
#endif
#ifndef FW_VERSION
#define FW_VERSION "0.1.0"
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif

namespace {

constexpr uint32_t NODE_ID          = 0xFFFF0000u + (uint32_t)HIL_INDEX;
constexpr uint8_t  BUTTON_PIN       = 0;        // BOOT button, active low
constexpr uint32_t STATUS_PERIOD_MS = 2000;
constexpr uint32_t UPDATE_PERIOD_MS = 100;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 60;
constexpr uint16_t MQTT_BUFFER_BYTES  = 2304;   // >= 2032-byte frame + MQTT overhead

char g_node_hex[9];
char g_topic_raw[32];
char g_topic_status[32];
char g_topic_cmd[32];
char g_client_id[24];

WiFiClient   g_tcp;
PubSubClient g_mqtt(g_tcp);
hil::Playback g_playback;

// ---- double buffer shared with the ISR --------------------------------------
hil::RawFrame g_frames[2];
volatile uint8_t  g_fill_buf  = 0;
volatile uint16_t g_fill_idx  = 0;
volatile bool     g_ready     = false;
volatile uint8_t  g_ready_buf = 0;
volatile uint32_t g_overruns  = 0;  // frames completed before the previous was published

hw_timer_t* g_timer = nullptr;

uint32_t g_seq = 0;
uint32_t g_frames_sent = 0;
uint32_t g_last_status_ms = 0;
uint32_t g_last_update_ms = 0;
uint32_t g_last_wifi_try_ms = 0;
uint32_t g_last_mqtt_try_ms = 0;
uint32_t g_button_change_ms = 0;
bool     g_button_last = true;   // pulled up

void IRAM_ATTR on_tick() {
  hil::RawFrame& f = g_frames[g_fill_buf];
  uint16_t i = g_fill_idx;
  if (i == 0) f.hdr.ts_us = (uint64_t)esp_timer_get_time();
  f.samples[i] = g_playback.next_sample();
  if (++i >= hil::FRAME_SAMPLES) {
    i = 0;
    if (g_ready) g_overruns = g_overruns + 1;
    g_ready_buf = g_fill_buf;
    g_ready = true;
    g_fill_buf = g_fill_buf ^ 1;
  }
  g_fill_idx = i;
}

void start_sampling() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  g_timer = timerBegin(1000000);                    // 1 MHz tick
  timerAttachInterrupt(g_timer, &on_tick);
  timerAlarm(g_timer, 1000000 / hil::SAMPLE_RATE_HZ, true, 0);
#else
  g_timer = timerBegin(0, 80, true);                // 80 MHz APB / 80 = 1 MHz
  timerAttachInterrupt(g_timer, &on_tick, true);
  timerAlarmWrite(g_timer, 1000000 / hil::SAMPLE_RATE_HZ, true);
  timerAlarmEnable(g_timer);
#endif
}

// ---- connectivity ----------------------------------------------------------
void ensure_wifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  const uint32_t now = millis();
  if (now - g_last_wifi_try_ms < 5000 && g_last_wifi_try_ms != 0) return;
  g_last_wifi_try_ms = now;
  Serial.printf("[wifi] connecting to %s ...\n", WIFI_SSID);
  WiFi.disconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void on_mqtt_message(char* topic, byte* payload, unsigned int len) {
  if (strcmp(topic, g_topic_cmd) != 0) return;
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, len);
  if (err) {
    Serial.printf("[cmd] bad JSON: %s\n", err.c_str());
    return;
  }
  int mode = doc["mode"] | -1;
  float intensity = doc["intensity"] | 1.0f;
  if (mode < 0 || mode > 3) {
    Serial.printf("[cmd] ignoring mode %d\n", mode);
    return;
  }
  g_playback.set_mode((hil::Mode)mode, intensity);
  Serial.printf("[cmd] mode=%d intensity=%.2f\n", mode, intensity);
}

bool ensure_mqtt() {
  if (WiFi.status() != WL_CONNECTED) return false;
  if (g_mqtt.connected()) return true;
  const uint32_t now = millis();
  if (now - g_last_mqtt_try_ms < 2000 && g_last_mqtt_try_ms != 0) return false;
  g_last_mqtt_try_ms = now;
  Serial.printf("[mqtt] connecting to %s:%d as %s ...\n", MQTT_HOST, (int)MQTT_PORT, g_client_id);
  if (g_mqtt.connect(g_client_id)) {
    g_mqtt.subscribe(g_topic_cmd, 1);
    Serial.printf("[mqtt] connected; subscribed %s\n", g_topic_cmd);
    g_last_status_ms = 0;  // publish status right away
    return true;
  }
  Serial.printf("[mqtt] failed, rc=%d\n", g_mqtt.state());
  return false;
}

void publish_status() {
  char buf[192];
  const int n = snprintf(buf, sizeof(buf),
      "{\"node_id\":%lu,\"mode\":%u,\"uptime_s\":%lu,\"rssi\":%d,\"fw\":\"%s\",\"ip\":\"%s\","
      "\"intensity\":%.2f,\"severity\":%.2f,\"frames\":%lu,\"overruns\":%lu}",
      (unsigned long)NODE_ID, (unsigned)g_playback.mode(), (unsigned long)(millis() / 1000),
      (int)WiFi.RSSI(), FW_VERSION, WiFi.localIP().toString().c_str(),
      g_playback.intensity(), g_playback.pathology().severity,
      (unsigned long)g_frames_sent, (unsigned long)g_overruns);
  if (n > 0) g_mqtt.publish(g_topic_status, (const uint8_t*)buf, (unsigned)n, /*retained=*/true);
}

void publish_frame(uint8_t idx) {
  hil::RawFrame& f = g_frames[idx];
  memcpy(f.hdr.magic, "HILR", 4);
  f.hdr.version        = hil::FRAME_VERSION;
  f.hdr.mode           = (uint8_t)g_playback.mode();
  f.hdr.reserved0      = 0;
  f.hdr.node_id        = NODE_ID;
  f.hdr.seq            = g_seq++;
  f.hdr.sample_rate_hz = hil::SAMPLE_RATE_HZ;
  f.hdr.n_samples      = hil::FRAME_SAMPLES;
  f.hdr.reserved1      = 0;
  if (g_mqtt.connected()) {
    if (g_mqtt.publish(g_topic_raw, (const uint8_t*)&f, sizeof(f), false)) g_frames_sent++;
  }
}

void poll_button() {
  const bool pressed = digitalRead(BUTTON_PIN) == LOW;
  const uint32_t now = millis();
  if (pressed == !g_button_last) return;  // no edge
  if (now - g_button_change_ms < BUTTON_DEBOUNCE_MS) return;
  g_button_change_ms = now;
  g_button_last = !pressed;
  if (pressed) {
    const hil::Mode m = hil::next_mode(g_playback.mode());
    g_playback.set_mode(m, hil::BUTTON_INTENSITY);
    Serial.printf("[button] mode -> %u\n", (unsigned)m);
    g_last_status_ms = 0;  // announce immediately
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  snprintf(g_node_hex, sizeof(g_node_hex), "%08lx", (unsigned long)NODE_ID);
  snprintf(g_topic_raw, sizeof(g_topic_raw), "hil/%s/raw", g_node_hex);
  snprintf(g_topic_status, sizeof(g_topic_status), "hil/%s/status", g_node_hex);
  snprintf(g_topic_cmd, sizeof(g_topic_cmd), "hil/%s/cmd", g_node_hex);
  snprintf(g_client_id, sizeof(g_client_id), "hil-%s", g_node_hex);
  Serial.printf("\n[hil] node %s (HIL_INDEX=%d) fw %s\n", g_node_hex, (int)HIL_INDEX, FW_VERSION);

  pinMode(BUTTON_PIN, INPUT_PULLUP);

  uint32_t seed = (uint32_t)ESP.getEfuseMac() ^ 0xA5A5A5A5u ^ (uint32_t)HIL_INDEX;
  g_playback.begin(seed);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower latency / jitter for the 10 Hz stream
  WiFi.setHostname(g_client_id);

  g_mqtt.setServer(MQTT_HOST, MQTT_PORT);
  g_mqtt.setBufferSize(MQTT_BUFFER_BYTES);
  g_mqtt.setKeepAlive(15);
  g_mqtt.setSocketTimeout(2);
  g_mqtt.setCallback(on_mqtt_message);

  start_sampling();
  Serial.println("[hil] sampling at 10 kHz");
}

void loop() {
  const uint32_t now = millis();

  ensure_wifi();
  if (ensure_mqtt()) g_mqtt.loop();

  poll_button();

  if (now - g_last_update_ms >= UPDATE_PERIOD_MS) {
    const float dt = (g_last_update_ms == 0) ? 0.f : (now - g_last_update_ms) * 1e-3f;
    g_last_update_ms = now;
    g_playback.update(dt);
  }

  if (g_ready) {
    const uint8_t idx = g_ready_buf;
    g_ready = false;
    publish_frame(idx);
  }

  if (g_mqtt.connected() && (g_last_status_ms == 0 || now - g_last_status_ms >= STATUS_PERIOD_MS)) {
    g_last_status_ms = now;
    publish_status();
  }
}
