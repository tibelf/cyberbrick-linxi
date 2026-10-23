"""虚拟设备身份：MAC + Client-Id，首次运行生成后持久化，保证在灵矽平台上是"同一台设备"。"""

import json
import os
import random
import uuid
from dataclasses import asdict, dataclass


@dataclass
class Identity:
    mac: str
    client_id: str


def _random_mac() -> str:
    # 本地管理 + 单播位（首字节 bit1=1, bit0=0），避免与真实网卡 MAC 冲突
    first = (random.randint(0, 255) | 0x02) & 0xFE
    rest = [random.randint(0, 255) for _ in range(5)]
    return ":".join(f"{b:02X}" for b in [first, *rest])


def load_or_create(path: str) -> Identity:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Identity(mac=data["mac"], client_id=data["client_id"])
    ident = Identity(mac=_random_mac(), client_id=str(uuid.uuid4()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(ident), f, indent=2)
    return ident
