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
<title>小车摄像头</title><style>
body{font-family:system-ui,sans-serif;margin:16px auto;padding:0 16px;max-width:640px;color:#222}
img{display:block;width:100%;background:#eee;border-radius:8px}label{display:flex;align-items:center;
justify-content:space-between;gap:16px;margin:16px 0}select,input{font-size:16px}#message{color:#555}
</style></head><body><h2>小车摄像头</h2><img id="view" alt="摄像头画面">
<p><a href="/capture">拍一张照片</a></p>
<label>白平衡<select id="wb_mode"><option value="0">自动</option><option value="1">晴天</option>
<option value="2">阴天</option><option value="3">办公室</option><option value="4">室内暖光</option></select></label>
<label>亮度<input id="brightness" type="range" min="-2" max="2" step="1"></label>
<label>饱和度<input id="saturation" type="range" min="-2" max="2" step="1"></label>
<label>水平镜像<input id="hmirror" type="checkbox"></label>
<label>上下翻转<input id="vflip" type="checkbox"></label>
<p id="message">调整会立即生效并保存到摄像头。</p><script>
document.getElementById('view').src='http://'+location.hostname+':81/stream';
const ids=['wb_mode','brightness','saturation','hmirror','vflip'];
fetch('/settings').then(r=>{if(!r.ok)throw Error();return r.json()}).then(s=>ids.forEach(id=>{
 const el=document.getElementById(id);if(el.type==='checkbox')el.checked=!!s[id];else el.value=s[id];
})).catch(()=>document.getElementById('message').textContent='暂时无法读取设置，请刷新页面。');
ids.forEach(id=>document.getElementById(id).addEventListener('change',async e=>{
 const el=e.target, value=el.type==='checkbox'?(el.checked?1:0):el.value;
 try{const r=await fetch('/control?var='+encodeURIComponent(id)+'&val='+encodeURIComponent(value));
 if(!r.ok)throw Error();document.getElementById('message').textContent='已调整并保存。';}
 catch(err){document.getElementById('message').textContent='调整或保存失败，请重试。';}
}));
</script></body></html>)HTML";
  httpd_resp_set_type(req, "text/html; charset=utf-8");
  return httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t settings_handler(httpd_req_t *req) {
  if (!take_camera_lock()) {
    httpd_resp_set_status(req, "503 Service Unavailable");
    return httpd_resp_sendstr(req, "camera unavailable");
  }
  sensor_t *sensor = esp_camera_sensor_get();
  char result[160];
  if (sensor) {
    snprintf(result, sizeof(result),
             "{\"wb_mode\":%u,\"brightness\":%d,\"saturation\":%d,\"hmirror\":%u,\"vflip\":%u}",
             sensor->status.wb_mode, sensor->status.brightness, sensor->status.saturation,
             sensor->status.hmirror, sensor->status.vflip);
  }
  xSemaphoreGive(camera_lock);
  if (!sensor) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "sensor unavailable");
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_sendstr(req, result);
}

static bool supported_setting(const char *variable, long value) {
  return (!strcmp(variable, "wb_mode") && value >= 0 && value <= 4) ||
         ((!strcmp(variable, "brightness") || !strcmp(variable, "saturation")) && value >= -2 && value <= 2) ||
         ((!strcmp(variable, "hmirror") || !strcmp(variable, "vflip")) && (value == 0 || value == 1));
}

static int apply_setting(sensor_t *sensor, const char *variable, int value) {
  if (!strcmp(variable, "wb_mode")) return sensor->set_wb_mode(sensor, value);
  if (!strcmp(variable, "brightness")) return sensor->set_brightness(sensor, value);
  if (!strcmp(variable, "saturation")) return sensor->set_saturation(sensor, value);
  if (!strcmp(variable, "hmirror")) return sensor->set_hmirror(sensor, value);
  if (!strcmp(variable, "vflip")) return sensor->set_vflip(sensor, value);
  return -1;
}

static void restore_image_settings() {
  sensor_t *sensor = camera_ready ? esp_camera_sensor_get() : nullptr;
  if (!sensor) return;
  Preferences prefs;
  if (!prefs.begin("camera-img", true)) return;
  const char *names[] = {"wb_mode", "brightness", "saturation", "hmirror", "vflip"};
  for (const char *name : names) {
    if (!prefs.isKey(name)) continue;
    int value = prefs.getInt(name);
    if (supported_setting(name, value)) apply_setting(sensor, name, value);
  }
  prefs.end();
}

static esp_err_t control_handler(httpd_req_t *req) {
  char query[96], variable[24], value_text[12];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
      httpd_query_key_value(query, "var", variable, sizeof(variable)) != ESP_OK ||
      httpd_query_key_value(query, "val", value_text, sizeof(value_text)) != ESP_OK) {
    return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "missing parameter");
  }
  char *end = nullptr;
  long value = strtol(value_text, &end, 10);
  if (!end || end == value_text || *end != '\0')
    return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid value");
  if (!supported_setting(variable, value)) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "unsupported setting");
  if (!take_camera_lock()) {
    httpd_resp_set_status(req, "503 Service Unavailable");
    return httpd_resp_sendstr(req, "camera unavailable");
  }
  sensor_t *sensor = esp_camera_sensor_get();
  int result = sensor ? apply_setting(sensor, variable, value) : -1;
  xSemaphoreGive(camera_lock);
  if (result != 0) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "setting failed");
  Preferences prefs;
  bool saved = false;
  if (prefs.begin("camera-img", false)) {
    saved = prefs.putInt(variable, value) == sizeof(int32_t);
    prefs.end();
  }
  if (!saved) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "setting applied but save failed");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_sendstr(req, "OK");
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
      const httpd_uri_t settings = { .uri = "/settings", .method = HTTP_GET, .handler = settings_handler, .user_ctx = nullptr };
      const httpd_uri_t control = { .uri = "/control", .method = HTTP_GET, .handler = control_handler, .user_ctx = nullptr };
      httpd_register_uri_handler(image_server, &index);
      httpd_register_uri_handler(image_server, &capture);
      httpd_register_uri_handler(image_server, &settings);
      httpd_register_uri_handler(image_server, &control);
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
  restore_image_settings();
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
