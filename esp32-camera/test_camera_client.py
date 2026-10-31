"""摄像头串口协议的主机端测试，不需要接硬件。"""

import unittest

from camera_client import CameraClient, MAX_JPEG_BYTES


class FakeSerial:
    def __init__(self, lines=(), data=b""):
        self.lines = list(lines)
        self.data = bytearray(data)
        self.writes = []

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)

    def readline(self, size):
        return self.lines.pop(0) if self.lines else b""

    def read(self, size):
        size = min(size, 3)
        result = bytes(self.data[:size])
        del self.data[:size]
        return result


class CameraClientTests(unittest.TestCase):
    def test_status_ignores_boot_messages(self):
        device = FakeSerial([b"CAMERA_BOOTED\n", b"STATUS camera=1 psram=1 sensor=38 wifi=0 ip=-\n"])
        self.assertIn("camera=1", CameraClient(device).status())
        self.assertEqual(device.writes, [b"STATUS\n"])

    def test_capture_reassembles_fragmented_jpeg(self):
        jpeg = b"\xff\xd8abcde\xff\xd9"
        device = FakeSerial([b"FRAME 9\n"], jpeg)
        self.assertEqual(CameraClient(device).capture(), jpeg)
        self.assertEqual(device.writes, [b"CAPTURE\n"])

    def test_capture_rejects_unreasonable_length(self):
        device = FakeSerial([f"FRAME {MAX_JPEG_BYTES + 1}\n".encode()])
        with self.assertRaises(ValueError):
            CameraClient(device).capture()

    def test_wifi_sends_lengths_then_private_payload(self):
        device = FakeSerial([b"WIFI_READY\n", b"WIFI_OK 192.168.1.12\n"])
        self.assertEqual(CameraClient(device).configure_wifi("测试", "example-pass"), "192.168.1.12")
        self.assertEqual(device.writes[0], b"WIFI 6 12\n")
        self.assertEqual(device.writes[1], "测试example-pass".encode())
        self.assertNotIn(b"example-pass", device.writes[0])


if __name__ == "__main__":
    unittest.main()
