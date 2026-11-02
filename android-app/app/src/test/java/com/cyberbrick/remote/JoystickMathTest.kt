package com.cyberbrick.remote

import org.junit.Assert.assertEquals
import org.junit.Test

class JoystickMathTest {
    @Test fun `死区及前后左右分别对应履带方向`() {
        assertEquals(0 to 0, JoystickMath.tracks(0.05f, 0.05f))
        assertEquals(40 to 40, JoystickMath.tracks(0f, 1f))
        assertEquals(-40 to -40, JoystickMath.tracks(0f, -1f))
        assertEquals(40 to -40, JoystickMath.tracks(1f, 0f))
        assertEquals(-40 to 40, JoystickMath.tracks(-1f, 0f))
        assertEquals(40 to 0, JoystickMath.tracks(0.5f, 0.5f))
        assertEquals(31 to 31, JoystickMath.tracks(0f, 0.5f))
    }

    @Test fun `触点超出圆圈时限制范围且屏幕向上代表前进`() {
        val (x, y) = JoystickMath.normalize(300f, -400f, 100f)
        assertEquals(0.6f, x, 0.001f)
        assertEquals(0.8f, y, 0.001f)
        assertEquals(0f to 0f, JoystickMath.normalize(20f, 20f, 0f))
        assertEquals(0 to 0, JoystickMath.tracks(Float.NaN, 1f))
    }
}
