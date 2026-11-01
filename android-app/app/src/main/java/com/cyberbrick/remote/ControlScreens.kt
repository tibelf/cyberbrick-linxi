package com.cyberbrick.remote

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.animation.core.animateOffsetAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.abs

@Composable
fun ControlScreen(state: CloudState,cloud: CloudClient,onVoice: () -> Unit,onFireDetails: () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal=20.dp,vertical=8.dp),
        verticalArrangement=Arrangement.spacedBy(10.dp)) {
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically) {
            SmallStatus(if(state.realCar)"真实小车在线" else "模拟小车",state.carOnline)
            Text(if(state.linx=="connected")"灵矽已就绪" else "语音连接中",fontSize=12.sp,color=Muted)
        }
        Surface(onClick=onVoice,enabled=state.audio!=null,shape=RoundedCornerShape(18.dp),color=Raised,
            border=BorderStroke(1.dp,Line),modifier=Modifier.fillMaxWidth()) {
            Row(Modifier.padding(8.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                Surface(color=Accent,shape=RoundedCornerShape(12.dp),modifier=Modifier.size(36.dp)) {
                    Box(contentAlignment=Alignment.Center){Glyph(Symbol.MIC,color=Ground)}
                }
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(4.dp)) {
                    Text("说句话，让小车动起来",fontSize=15.sp,fontWeight=FontWeight.SemiBold)
                    Text("点击开始，再次点击结束",fontSize=11.sp,color=Muted)
                }
                Glyph(Symbol.RIGHT,Modifier.size(18.dp),Muted)
            }
        }
        Column(Modifier.fillMaxWidth(),verticalArrangement=Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween) {
                Text("驾驶",fontSize=15.sp,fontWeight=FontWeight.SemiBold)
                Text("拖动摇杆 · 松手停车",fontSize=11.sp,color=Muted)
            }
            DriveJoystick(cloud)
        }
        Column(verticalArrangement=Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.SpaceBetween) {
                Text("炮台上下",fontSize=14.sp,fontWeight=FontWeight.SemiBold)
                TextButton(onClick=onFireDetails,modifier=Modifier.height(32.dp),contentPadding=PaddingValues(horizontal=6.dp)) {Text("展开控制",fontSize=11.sp)}
            }
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                BarrelButton("抬高一点",Symbol.UP,{cloud.barrel("up")},Modifier.weight(1f))
                BarrelButton("压低一点",Symbol.DOWN,{cloud.barrel("down")},Modifier.weight(1f))
            }
        }
        Row(horizontalArrangement=Arrangement.spacedBy(10.dp)) {
            OutlinedButton(onClick={cloud.arm(!state.armed); if(!state.armed)onFireDetails()},enabled=state.fireAvailable,
                modifier=Modifier.weight(1f).heightIn(min=48.dp),shape=RoundedCornerShape(14.dp)) {
                Glyph(if(state.armed)Symbol.UNLOCK else Symbol.LOCK,Modifier.size(17.dp),if(state.fireAvailable)Accent else Muted)
                Spacer(Modifier.width(6.dp));Text(if(state.armed)"锁定发射" else "启用发射",fontSize=12.sp)
            }
            Button(onClick=cloud::fire,enabled=state.armed && state.cooldownMs==0L,modifier=Modifier.weight(1f).heightIn(min=48.dp),shape=RoundedCornerShape(14.dp)) {
                Text(if(state.cooldownMs>0)"冷却中…" else if(state.armed)"发射一发" else "单发 · 已锁定",fontSize=13.sp,fontWeight=FontWeight.SemiBold)
            }
        }
        Text(state.message,fontSize=11.sp,color=Muted,lineHeight=18.sp,modifier=Modifier.padding(bottom=8.dp))
    }
}

@Composable
private fun DriveJoystick(cloud: CloudClient) {
    var touching by remember { mutableStateOf(false) }
    var position by remember { mutableStateOf(Offset.Zero) }
    var tracks by remember { mutableStateOf(0 to 0) }
    var accessiblePulse by remember { mutableStateOf<Job?>(null) }
    val scope = rememberCoroutineScope()
    val knob by animateOffsetAsState(position,
        animationSpec = if (touching) snap() else spring(dampingRatio = 0.72f, stiffness = 380f), label = "摇杆回中")

    fun pulse(left: Int, right: Int): Boolean {
        accessiblePulse?.cancel()
        cloud.steer(left, right)
        accessiblePulse = scope.launch { delay(220); cloud.stop() }
        return true
    }
    DisposableEffect(cloud) {
        onDispose { accessiblePulse?.cancel(); cloud.stop() }
    }

    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            TrackIndicator("左", tracks.first)
            Spacer(Modifier.width(10.dp))
            Box(Modifier.weight(1f, fill = false).widthIn(max = 225.dp).aspectRatio(1f)) {
                Canvas(Modifier.fillMaxSize().semantics {
                    contentDescription = "驾驶摇杆，向前后左右拖动，松手停车"
                    customActions = listOf(
                        CustomAccessibilityAction("短暂前进") { pulse(40, 40) },
                        CustomAccessibilityAction("短暂后退") { pulse(-40, -40) },
                        CustomAccessibilityAction("短暂左转") { pulse(-40, 40) },
                        CustomAccessibilityAction("短暂右转") { pulse(40, -40) },
                        CustomAccessibilityAction("停止") { accessiblePulse?.cancel(); cloud.stop(); true },
                    )
                }.pointerInput(cloud) {
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        accessiblePulse?.cancel()
                        touching = true
                        fun update(point: Offset) {
                            val radius = minOf(size.width, size.height) * 0.35f
                            val (x, y) = JoystickMath.normalize(point.x - size.width / 2f, point.y - size.height / 2f, radius)
                            position = Offset(x, -y)
                            tracks = JoystickMath.tracks(x, y)
                            cloud.steer(tracks.first, tracks.second)
                        }
                        try {
                            down.consume()
                            update(down.position)
                            while (true) {
                                val change = awaitPointerEvent().changes.firstOrNull { it.id == down.id } ?: break
                                if (!change.pressed) break
                                change.consume()
                                update(change.position)
                            }
                        } finally {
                            touching = false
                            position = Offset.Zero
                            tracks = 0 to 0
                            cloud.stop()
                        }
                    }
                }) {
                    val radius = size.minDimension / 2f
                    val middle = center
                    drawCircle(brush = Brush.radialGradient(listOf(Color(0xFF343C46), Panel), middle, radius), radius = radius)
                    drawCircle(Line, radius = radius - 1.dp.toPx(), style = Stroke(width = 1.5.dp.toPx()))
                    drawLine(Line, Offset(middle.x, middle.y - radius * .78f), Offset(middle.x, middle.y + radius * .78f), 1.dp.toPx())
                    drawLine(Line, Offset(middle.x - radius * .78f, middle.y), Offset(middle.x + radius * .78f, middle.y), 1.dp.toPx())
                    drawCircle(Line, radius = radius * .55f, style = Stroke(width = 1.dp.toPx(),
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(5.dp.toPx(), 5.dp.toPx()))))
                    val point = middle + Offset(knob.x * radius * .70f, knob.y * radius * .70f)
                    drawCircle(Accent.copy(alpha = if (touching) .24f else .12f), radius = radius * .36f, center = point)
                    drawCircle(brush = Brush.radialGradient(listOf(Color(0xFFFFE281), Accent, Color(0xFFB39231)), point, radius * .31f),
                        radius = radius * .30f, center = point)
                    drawCircle(Color(0xFF6E5B27), radius = radius * .30f, center = point, style = Stroke(1.dp.toPx()))
                }
                Text("前进", Modifier.align(Alignment.TopCenter).padding(top = 12.dp), color = Muted, fontSize = 11.sp)
                Text("后退", Modifier.align(Alignment.BottomCenter).padding(bottom = 12.dp), color = Muted, fontSize = 11.sp)
                Text("左", Modifier.align(Alignment.CenterStart).padding(start = 14.dp), color = Muted, fontSize = 11.sp)
                Text("右", Modifier.align(Alignment.CenterEnd).padding(end = 14.dp), color = Muted, fontSize = 11.sp)
            }
            Spacer(Modifier.width(10.dp))
            TrackIndicator("右", tracks.second)
        }
        Text(if (touching) "松手停车" else "按住拖动，松手回中", color = Muted, fontSize = 11.sp)
    }
}

@Composable
private fun TrackIndicator(label: String, value: Int) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(label, color = Muted, fontSize = 11.sp)
        Canvas(Modifier.size(width = 12.dp, height = 112.dp)) {
            drawRoundRect(Raised, cornerRadius = CornerRadius(size.width / 2))
            drawLine(Line, Offset(0f, size.height / 2), Offset(size.width, size.height / 2), 1.dp.toPx())
            if (value != 0) {
                val height = size.height / 2 * abs(value) / JoystickMath.LIMIT
                drawRoundRect(if (value > 0) Color(0xFF78C89A) else Accent,
                    topLeft = Offset(0f, if (value > 0) size.height / 2 - height else size.height / 2),
                    size = Size(size.width, height), cornerRadius = CornerRadius(size.width / 2))
            }
        }
        Text(if (value > 0) "+$value" else "$value", color = Muted, fontSize = 11.sp)
    }
}

@Composable
fun BarrelButton(label: String,symbol: Symbol,onClick: () -> Unit,modifier: Modifier=Modifier,enabled: Boolean=true) {
    OutlinedButton(onClick=onClick,enabled=enabled,modifier=modifier.heightIn(min=48.dp),shape=RoundedCornerShape(13.dp),
        border=BorderStroke(1.dp,Line),colors=ButtonDefaults.outlinedButtonColors(contentColor=Color(0xFFEDF0F5))) {
        Glyph(symbol,Modifier.size(17.dp));Spacer(Modifier.width(8.dp));Text(label,fontSize=12.sp)
    }
}

@Composable
fun VoiceScreen(cloud: CloudState,audio: NativeAudioState,onTalk: () -> Unit) {
    val replying=cloud.speaking || audio.playing
    val ready=cloud.canControl && cloud.audio!=null
    val title=when {
        audio.preparing -> "正在打开麦克风"
        audio.recording -> "正在听你说"
        replying -> "小车正在回复"
        !ready -> "先连接小车与语音"
        audio.seconds>0 && cloud.botText.isEmpty() -> "收音已结束"
        else -> "点一下，开始说话"
    }
    Column(Modifier.fillMaxSize().padding(horizontal=20.dp,vertical=8.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween) {
            Text(if(cloud.linx=="connected")"灵矽已连接"else"灵矽未连接",fontSize=12.sp,color=Muted)
            Text(if(cloud.carOnline)"小车在线"else"小车未上线",fontSize=12.sp,color=Muted)
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()),horizontalAlignment=Alignment.CenterHorizontally,
            verticalArrangement=Arrangement.spacedBy(16.dp)) {
            Spacer(Modifier.height(12.dp))
            Surface(color=if(audio.recording)StopBackground else Color(0xFF2C2B25),shape=RoundedCornerShape(32.dp),modifier=Modifier.size(96.dp)) {
                Box(contentAlignment=Alignment.Center) {Glyph(Symbol.MIC,Modifier.size(42.dp),if(audio.recording)StopInk else Accent)}
            }
            Text(title,fontSize=25.sp,fontWeight=FontWeight.SemiBold)
            Text(when { audio.recording -> "说完后点击「结束说话」";replying -> "回复播完后可再次说话";else -> "例如：前进一秒，然后停下。" },fontSize=12.sp,color=Muted)
            Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(10.dp),modifier=Modifier.fillMaxWidth()) {
                Text("麦克风",fontSize=11.sp,color=Muted)
                LinearProgressIndicator(progress={audio.level},modifier=Modifier.weight(1f).height(6.dp),color=if(audio.recording)StopInk else Accent,trackColor=Raised)
                Text("00:${audio.seconds.toString().padStart(2,'0')}",fontSize=11.sp,color=Muted)
            }
            TranscriptCard("识别内容",cloud.userText.ifEmpty { "识别结果会显示在这里" })
            TranscriptCard("小车回复",cloud.botText.ifEmpty { "语音回复和文字会显示在这里" })
        }
        Button(onClick=onTalk,enabled=audio.recording || audio.preparing || (ready && !replying),
            modifier=Modifier.fillMaxWidth().heightIn(min=60.dp),shape=RoundedCornerShape(17.dp),
            colors=ButtonDefaults.buttonColors(containerColor=if(audio.recording)Color(0xFF743D3C)else Accent,
                contentColor=if(audio.recording)Color(0xFFFFF1ED)else Ground)) {
            if(audio.preparing) CircularProgressIndicator(Modifier.size(18.dp),strokeWidth=2.dp,color=Ground)
            else Glyph(if(audio.recording)Symbol.STOP else Symbol.MIC,color=if(audio.recording)Color.White else Ground)
            Spacer(Modifier.width(9.dp));Text(when {audio.preparing ->"取消打开";audio.recording->"结束说话";replying->"小车正在回复…";else->"点击开始说话"},fontSize=16.sp,fontWeight=FontWeight.Bold)
        }
        Text("点一下开始，再点一下结束，不用长按。",Modifier.align(Alignment.CenterHorizontally),fontSize=11.sp,color=Muted)
    }
}

@Composable
private fun TranscriptCard(label: String,text: String) {
    Surface(color=Panel,shape=RoundedCornerShape(18.dp),border=BorderStroke(1.dp,Line),modifier=Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
            Text(label,color=Accent,fontSize=11.sp,fontWeight=FontWeight.SemiBold)
            Text(text,color=Color(0xFFE2E6EE),fontSize=14.sp,lineHeight=23.sp)
        }
    }
}

@Composable
fun OfflineScreen(state: CloudState,onSettings: () -> Unit,onRetry: () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement=Arrangement.spacedBy(18.dp),horizontalAlignment=Alignment.CenterHorizontally) {
        Spacer(Modifier.height(12.dp))
        Glyph(Symbol.TANK,Modifier.size(64.dp),Muted)
        Text(if(state.connected)"小车暂时离线"else when(state.phase){LinkPhase.CONNECTING->"正在连接云端";LinkPhase.RECONNECTING->"正在恢复连接";else->"连接你的 CyberBrick"},fontSize=24.sp,fontWeight=FontWeight.SemiBold)
        if(state.phase in listOf(LinkPhase.CONNECTING,LinkPhase.RECONNECTING)) CircularProgressIndicator(color=Accent,modifier=Modifier.size(26.dp))
        Text(if(state.connected)"云端服务已连接，正在等待小车上线。" else state.message,color=Muted,fontSize=13.sp,lineHeight=22.sp)
        Surface(color=Panel,shape=RoundedCornerShape(20.dp),border=BorderStroke(1.dp,Line),modifier=Modifier.fillMaxWidth()) {
            Column(Modifier.padding(18.dp),verticalArrangement=Arrangement.spacedBy(14.dp)) {
                Text("检查小车电源",fontSize=14.sp,fontWeight=FontWeight.SemiBold)
                Text("确认小车已开机、电池有电。",fontSize=12.sp,color=Muted,lineHeight=20.sp)
                HorizontalDivider(color=Line)
                Text("保持原热点开启",fontSize=14.sp,fontWeight=FontWeight.SemiBold)
                Text("使用已配置的 2.4 GHz 热点，保持名称、密码不变并能上网。",fontSize=12.sp,color=Muted,lineHeight=20.sp)
            }
        }
        Button(onClick=onRetry,modifier=Modifier.fillMaxWidth().heightIn(min=52.dp),shape=RoundedCornerShape(15.dp)) {Text(if(state.connected)"重新检查连接"else"连接云端")}
        OutlinedButton(onClick=onSettings,modifier=Modifier.fillMaxWidth().heightIn(min=48.dp),shape=RoundedCornerShape(15.dp)){Text("连接设置")}
        Text("操作已暂停，发射已锁定。小车上线后再重新发出指令。",fontSize=11.sp,color=Muted,lineHeight=19.sp)
    }
}
