// ESP32-CAM（AI-Thinker 引脚）车载摄像头：USB 验证、Wi-Fi 快照和实时画面。
// 摄像头只提供画面，不接收车辆控制命令。Wi-Fi 信息只通过串口配置并保存在板上。

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include "esp_camera.h"
#include "esp_http_server.h"

static constexpr int PWDN_PIN = 32;
static constexpr int XCLK_PIN = 0;
static constexpr int SIOD_PIN = 26;
static constexpr int SIOC_PIN = 27;
static constexpr int VSYNC_PIN = 25;
static constexpr int HREF_PIN = 23;
static constexpr int PCLK_PIN = 22;
static constexpr int FLASH_LED_PIN = 4;

static SemaphoreHandle_t camera_lock = nullptr;
static httpd_handle_t image_server = nullptr;
static httpd_handle_t stream_server = nullptr;
static bool camera_ready = false;

static bool take_camera_lock() {
  return camera_ready && xSemaphoreTake(camera_lock, pdMS_TO_TICKS(2000)) == pdTRUE;
}

static esp_err_t index_handler(httpd_req_t *req) {
  static const char page[] = R"HTML(<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>小车摄像头</title></head><body style="font-family:sans-serif;text-align:center">
<h2>小车摄像头</h2><img id="view" style="max-width:100%" alt="摄像头画面">
<p><a href="/capture">拍一张照片</a></p><script>
document.getElementById('view').src='http://'+location.hostname+':81/stream';
</script></body></html>)HTML";
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t capture_handler(httpd_req_t *req) {
  if (!take_camera_lock()) {
    httpd_resp_set_status(req, "503 Service Unavailable");
    httpd_resp_sendstr(req, "camera unavailable");
    return ESP_FAIL;
  }
  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame) {
    xSemaphoreGive(camera_lock);
    httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "capture failed");
    return ESP_FAIL;
  }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  esp_err_t result = httpd_resp_send(req, (const char *)frame->buf, frame->len);
  esp_camera_fb_return(frame);
  xSemaphoreGive(camera_lock);
  return result;
}

static esp_err_t stream_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "multipart/x-mixed-replace; boundary=frame");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  while (WiFi.status() == WL_CONNECTED) {
    if (!take_camera_lock()) return ESP_FAIL;
    camera_fb_t *frame = esp_camera_fb_get();
    if (!frame) {
      xSemaphoreGive(camera_lock);
      return ESP_FAIL;
    }
    char header[96];
    int length = snprintf(header, sizeof(header),
                          "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                          (unsigned)frame->len);
    esp_err_t result = httpd_resp_send_chunk(req, header, length);
    if (result == ESP_OK) result = httpd_resp_send_chunk(req, (const char *)frame->buf, frame->len);
    if (result == ESP_OK) result = httpd_resp_send_chunk(req, "\r\n", 2);
    esp_camera_fb_return(frame);
    xSemaphoreGive(camera_lock);
    if (result != ESP_OK) return result;
    vTaskDelay(pdMS_TO_TICKS(100));
  }
  return ESP_OK;
}

static void start_servers() {
  if (!camera_ready || WiFi.status() != WL_CONNECTED) return;
  if (!image_server) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    if (httpd_start(&image_server, &config) == ESP_OK) {
      const httpd_uri_t index = { .uri = "/", .method = HTTP_GET, .handler = index_handler, .user_ctx = nullptr };
      const httpd_uri_t capture = { .uri = "/capture", .method = HTTP_GET, .handler = capture_handler, .user_ctx = nullptr };
      httpd_register_uri_handler(image_server, &index);
      httpd_register_uri_handler(image_server, &capture);
    }
  }
  if (!stream_server) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 81;
    config.ctrl_port++;
    if (httpd_start(&stream_server, &config) == ESP_OK) {
      const httpd_uri_t stream = { .uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = nullptr };
      httpd_register_uri_handler(stream_server, &stream);
    }
  }
}

static bool setup_camera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5;
  config.pin_d1 = 18;
  config.pin_d2 = 19;
  config.pin_d3 = 21;
  config.pin_d4 = 36;
  config.pin_d5 = 39;
  config.pin_d6 = 34;
  config.pin_d7 = 35;
  config.pin_xclk = XCLK_PIN;
  config.pin_pclk = PCLK_PIN;
  config.pin_vsync = VSYNC_PIN;
  config.pin_href = HREF_PIN;
  config.pin_sccb_sda = SIOD_PIN;
  config.pin_sccb_scl = SIOC_PIN;
  config.pin_pwdn = PWDN_PIN;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = psramFound() ? FRAMESIZE_VGA : FRAMESIZE_QVGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  esp_err_t result = esp_camera_init(&config);
  if (result != ESP_OK) {
    Serial.printf("CAMERA_ERROR %d\n", (int)result);
    return false;
  }
  return true;
}

static void report_status() {
  sensor_t *sensor = camera_ready ? esp_camera_sensor_get() : nullptr;
  Serial.printf("STATUS camera=%d psram=%d sensor=%u wifi=%d ip=%s\n",
                camera_ready ? 1 : 0, psramFound() ? 1 : 0,
                sensor ? (unsigned)sensor->id.PID : 0,
                WiFi.status() == WL_CONNECTED ? 1 : 0,
                WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : "-");
}

static void serial_capture() {
  if (!take_camera_lock()) {
    Serial.println("ERROR camera_unavailable");
    return;
  }
  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame) {
    xSemaphoreGive(camera_lock);
    Serial.println("ERROR capture_failed");
    return;
  }
  Serial.printf("FRAME %u\n", (unsigned)frame->len);
  Serial.write(frame->buf, frame->len);
  Serial.flush();
  Serial.print("\nFRAME_END\n");
  esp_camera_fb_return(frame);
  xSemaphoreGive(camera_lock);
}

static void configure_wifi(const String &command) {
  unsigned ssid_len = 0, pass_len = 0;
  if (sscanf(command.c_str(), "WIFI %u %u", &ssid_len, &pass_len) != 2 ||
      ssid_len == 0 || ssid_len > 32 || pass_len > 63) {
    Serial.println("ERROR invalid_wifi_lengths");
    return;
  }
  char ssid[33] = {}, password[64] = {};
  Serial.println("WIFI_READY");
  Serial.setTimeout(15000);
  if (Serial.readBytes(ssid, ssid_len) != ssid_len ||
      Serial.readBytes(password, pass_len) != pass_len) {
    memset(password, 0, sizeof(password));
    Serial.println("ERROR wifi_input_timeout");
    return;
  }
  WiFi.disconnect();
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 20000) delay(100);
  if (WiFi.status() == WL_CONNECTED) {
    Preferences prefs;
    if (prefs.begin("camera-wifi", false)) {
      prefs.putString("ssid", ssid);
      prefs.putString("password", password);
      prefs.end();
    }
    start_servers();
    Serial.printf("WIFI_OK %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("ERROR wifi_connect_failed");
  }
  memset(password, 0, sizeof(password));
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(1000);
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);
  camera_lock = xSemaphoreCreateMutex();
  camera_ready = camera_lock && setup_camera();
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  Preferences prefs;
  if (prefs.begin("camera-wifi", true)) {
    String ssid = prefs.getString("ssid", "");
    String password = prefs.getString("password", "");
    prefs.end();
    if (ssid.length()) {
      WiFi.mode(WIFI_STA);
      WiFi.begin(ssid.c_str(), password.c_str());
    }
  }
  Serial.println("CAMERA_BOOTED");
}

void loop() {
  start_servers();
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    if (command == "STATUS") report_status();
    else if (command == "CAPTURE") serial_capture();
    else if (command.startsWith("WIFI ")) configure_wifi(command);
    else Serial.println("ERROR unknown_command");
  }
  delay(20);
}
