package com.cyberbrick.remote

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp

val Ground = Color(0xFF12151A)
val Panel = Color(0xFF1D222A)
val Raised = Color(0xFF272E38)
val Line = Color(0xFF3B4350)
val Muted = Color(0xFFB5BECC)
val Accent = Color(0xFFE8C547)
val StopBackground = Color(0xFF42282B)
val StopInk = Color(0xFFFFD6D0)

@Composable
fun CyberBrickTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = darkColorScheme(primary = Accent, onPrimary = Ground,
        background = Ground, onBackground = Color(0xFFF2F3F5), surface = Panel,
        onSurface = Color(0xFFF2F3F5), surfaceVariant = Raised, onSurfaceVariant = Muted,
        outline = Line, error = StopInk, errorContainer = StopBackground), content = content)
}

enum class Symbol { UP, DOWN, LEFT, RIGHT, MIC, STOP, LOCK, UNLOCK, GAMEPAD, LINK, SETTINGS, CLOSE, POWER, CHECK, BOLT, TANK }

/** 简洁原生线性图标，与设计稿保持一致。文字由所在控件提供无障碍标签。 */
@Composable
fun Glyph(symbol: Symbol, modifier: Modifier = Modifier.size(22.dp), color: Color = MaterialTheme.colorScheme.onSurface) {
    Canvas(modifier) {
        val sx = size.width / 24f
        val sy = size.height / 24f
        val width = 1.8f * sx
        fun p(x: Float, y: Float) = Offset(x * sx, y * sy)
        fun line(x1: Float, y1: Float, x2: Float, y2: Float) = drawLine(color, p(x1,y1),p(x2,y2),width,StrokeCap.Round)
        fun box(x: Float, y: Float, w: Float, h: Float, radius: Float = 2f) = drawRoundRect(color,p(x,y),Size(w*sx,h*sy),CornerRadius(radius*sx,radius*sy),style=Stroke(width))
        fun shape(vararg points: Pair<Float,Float>, close: Boolean = false) {
            val path=Path(); points.forEachIndexed { index, value -> if(index==0)path.moveTo(value.first*sx,value.second*sy) else path.lineTo(value.first*sx,value.second*sy) }; if(close)path.close()
            drawPath(path,color,style=Stroke(width,cap=StrokeCap.Round))
        }
        when(symbol) {
            Symbol.UP -> { line(12f,20f,12f,4f); shape(6f to 10f,12f to 4f,18f to 10f) }
            Symbol.DOWN -> { line(12f,4f,12f,20f); shape(6f to 14f,12f to 20f,18f to 14f) }
            Symbol.LEFT -> { line(20f,12f,4f,12f); shape(10f to 6f,4f to 12f,10f to 18f) }
            Symbol.RIGHT -> { line(4f,12f,20f,12f); shape(14f to 6f,20f to 12f,14f to 18f) }
            Symbol.MIC -> { box(9f,3f,6f,11f,3f); drawArc(color,0f,180f,false,p(6f,6f),Size(12f*sx,12f*sy),style=Stroke(width)); line(12f,18f,12f,21f); line(9f,21f,15f,21f) }
            Symbol.STOP -> { shape(8f to 3f,16f to 3f,21f to 8f,21f to 16f,16f to 21f,8f to 21f,3f to 16f,3f to 8f,close=true); box(9f,9f,6f,6f,1f) }
            Symbol.LOCK, Symbol.UNLOCK -> { box(5f,10f,14f,11f,3f); drawArc(color,180f,180f,false,p(8f,3f),Size(8f*sx,8f*sy),style=Stroke(width)); line(8f,7f,8f,10f); if(symbol==Symbol.LOCK)line(16f,7f,16f,10f) }
            Symbol.GAMEPAD -> { box(3f,6f,18f,13f,4f); line(7f,12f,12f,12f); line(9.5f,9.5f,9.5f,14.5f); line(16f,10f,17f,10f); line(17f,14f,18f,14f) }
            Symbol.LINK -> { box(2f,10f,12f,8f,4f); box(10f,6f,12f,8f,4f); line(8f,14f,16f,10f) }
            Symbol.SETTINGS -> { line(4f,7f,20f,7f); line(4f,17f,20f,17f); drawCircle(Ground,3f*sx,p(9f,7f)); drawCircle(color,3f*sx,p(9f,7f),style=Stroke(width)); drawCircle(Ground,3f*sx,p(15f,17f)); drawCircle(color,3f*sx,p(15f,17f),style=Stroke(width)) }
            Symbol.CLOSE -> { line(6f,6f,18f,18f); line(18f,6f,6f,18f) }
            Symbol.POWER -> { drawArc(color,-45f,270f,false,p(3f,3f),Size(18f*sx,18f*sy),style=Stroke(width)); line(12f,2f,12f,11f) }
            Symbol.CHECK -> { drawCircle(color,8f*sx,p(12f,12f),style=Stroke(width)); shape(8f to 12f,11f to 15f,16f to 9f) }
            Symbol.BOLT -> shape(13f to 2f,5f to 14f,11f to 14f,10f to 22f,19f to 9f,13f to 9f,close=true)
            Symbol.TANK -> { box(4f,7f,16f,11f,3f); shape(8f to 7f,8f to 4f,16f to 4f,16f to 7f); line(12f,4f,12f,1f); line(7f,11f,7f,14f); line(17f,11f,17f,14f); line(8f,18f,8f,21f); line(16f,18f,16f,21f) }
        }
    }
}

@Composable
fun BrandMark() {
    Box(Modifier.size(38.dp).background(Accent,RoundedCornerShape(11.dp))) {
        Glyph(Symbol.TANK,Modifier.size(28.dp).then(Modifier.align(androidx.compose.ui.Alignment.Center)),Ground)
    }
}
