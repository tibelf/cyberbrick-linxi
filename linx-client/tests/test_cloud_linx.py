"""云端灵矽地址和绑定流程的离线测试。"""

import pytest

from linx_client.cloud_linx import make_cloud_endpoint
from linx_client.identity import Identity


def test_cloud_endpoint_binding_and_encrypted_connection(monkeypatch):
    ident = Identity("02:00:00:00:00:01", "client")
    codes = []
    monkeypatch.setattr("linx_client.cloud_linx.ota.report", lambda _: {"activation": {"code": "123456"}})
    endpoint = make_cloud_endpoint(ident, codes.append)
    with pytest.raises(RuntimeError, match="尚未绑定"):
        endpoint()
    assert codes == ["123456"]

    monkeypatch.setattr("linx_client.cloud_linx.ota.report", lambda _: {
        "websocket": {"url": "ws://xrobo-io.qiniuapi.com/v1/ws/", "token": "secret"}})
    assert endpoint() == ("wss://xrobo-io.qiniuapi.com/v1/ws/", "secret")


@pytest.mark.parametrize("url", ["ws://other.example/ws", "wss://user:secret@example/ws",
                                  "wss://example/ws?device-id=secret"])
def test_cloud_endpoint_rejects_insecure_or_secret_bearing_urls(monkeypatch, url):
    monkeypatch.setattr("linx_client.cloud_linx.ota.report", lambda _: {"websocket": {"url": url, "token": "x"}})
    endpoint = make_cloud_endpoint(Identity("02:00:00:00:00:01", "client"), lambda _: None)
    with pytest.raises(RuntimeError):
        endpoint()
