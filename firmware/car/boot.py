# -*-coding:utf-8-*-
#
# The CyberBrick Codebase License, see the file LICENSE for details.
#
# Copyright (c) 2025 MakerWorld
#
# This file is executed on every boot (including wake-boot from deepsleep)
#
# 在原厂 boot.py（firmware/backup-car/boot.py）基础上加了模式选择：
#   有 /wifi.json 且开机时没按住板载按键（Pin9）→ Wi-Fi 控车（/app/wifi_main.py）
#   否则，或 Wi-Fi 模式启动失败 → 原厂遥控程序
# 恢复原厂：python -m linx_client wifi-setup --uninstall

import bbl_product
import sys
import os
import machine

_PRODUCT_NAME = "RC"
_PRODUCT_VERSION = "01.00.01.01"

bbl_product.set_app_name(_PRODUCT_NAME)
bbl_product.set_app_version(_PRODUCT_VERSION)
del bbl_product
del _PRODUCT_NAME
del _PRODUCT_VERSION

sys.path.append('/app')


def _wifi_mode():
    try:
        os.stat('/wifi.json')
    except OSError:
        return False
    return machine.Pin(9, machine.Pin.IN).value() == 1  # 按下为 0


if _wifi_mode():
    try:
        import wifi_main
        wifi_main.main()
    except Exception as e:
        print('[boot] Wi-Fi 模式失败，回到原厂遥控：', e)

import rc_main

rc_main.main()
