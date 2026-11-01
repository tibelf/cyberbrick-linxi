package com.cyberbrick.remote

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.roundToInt
import kotlin.math.sign

/** 沿用网页手柄的圆形限制、12% 死区及双履带混控，力度不超过云端上限 40。 */
object JoystickMath {
    const val LIMIT = 40
    private const val DEAD = 0.12f
    private const val TRACK_DEAD = 0.08f
    private const val START = LIMIT * 0.55f

    /** 输入是以圆心为原点的像素偏移，输出 x 向右、y 向前。 */
    fun normalize(dx: Float, dy: Float, radius: Float): Pair<Float, Float> {
        if (radius <= 0f || !dx.isFinite() || !dy.isFinite()) return 0f to 0f
        val distance = hypot(dx, dy)
        val scale = if (distance > radius) radius / distance else 1f
        return dx * scale / radius to -dy * scale / radius
    }

    fun tracks(x: Float, y: Float): Pair<Int, Int> {
        if (!x.isFinite() || !y.isFinite() || hypot(x, y) < DEAD) return 0 to 0
        fun power(value: Float): Int {
            val v = value.coerceIn(-1f, 1f)
            if (abs(v) < TRACK_DEAD) return 0
            return (sign(v) * (START + (LIMIT - START) * abs(v))).roundToInt().coerceIn(-LIMIT, LIMIT)
        }
        return power(y + x) to power(y - x)
    }
}
