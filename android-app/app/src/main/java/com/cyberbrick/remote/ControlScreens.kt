package com.cyberbrick.remote

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun ControlScreen(state: CloudState,cloud: CloudClient,onVoice: () -> Unit,onFireDetails: () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal=20.dp,vertical=8.dp),
        verticalArrangement=Arrangement.spacedBy(14.dp)) {
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically) {
            SmallStatus(if(state.realCar)"真实小车在线" else "模拟小车",state.carOnline)
            Text(if(state.linx=="connected")"灵矽已就绪" else "语音连接中",fontSize=12.sp,color=Muted)
        }
        Surface(onClick=onVoice,enabled=state.audio!=null,shape=RoundedCornerShape(18.dp),color=Raised,
            border=BorderStroke(1.dp,Line),modifier=Modifier.fillMaxWidth()) {
            Row(Modifier.padding(12.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                Surface(color=Accent,shape=RoundedCornerShape(13.dp),modifier=Modifier.size(40.dp)) {
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
                Text("按住移动 · 松手停车",fontSize=11.sp,color=Muted)
            }
            Column(Modifier.align(Alignment.CenterHorizontally),horizontalAlignment=Alignment.CenterHorizontally,
                verticalArrangement=Arrangement.spacedBy(6.dp)) {
                DirectionKey("前进",Symbol.UP,{cloud.drive("forward")},cloud::stop)
                Row(horizontalArrangement=Arrangement.spacedBy(6.dp)) {
                    DirectionKey("左转",Symbol.LEFT,{cloud.drive("left")},cloud::stop)
                    Button(onClick=cloud::stop,modifier=Modifier.size(82.dp,60.dp),shape=RoundedCornerShape(17.dp),
                        contentPadding=PaddingValues(0.dp),colors=ButtonDefaults.buttonColors(containerColor=StopBackground,contentColor=StopInk)) {
                        Column(horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(4.dp)) {
                            Glyph(Symbol.STOP,Modifier.size(18.dp),StopInk);Text("停止",fontSize=12.sp)
                        }
                    }
                    DirectionKey("右转",Symbol.RIGHT,{cloud.drive("right")},cloud::stop)
                }
                DirectionKey("后退",Symbol.DOWN,{cloud.drive("backward")},cloud::stop)
            }
            Text(state.motion,Modifier.align(Alignment.CenterHorizontally),fontSize=11.sp,color=Muted)
        }
        Column(verticalArrangement=Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.SpaceBetween) {
                Text("炮台上下",fontSize=14.sp,fontWeight=FontWeight.SemiBold)
                TextButton(onClick=onFireDetails,contentPadding=PaddingValues(horizontal=6.dp)) {Text("展开控制",fontSize=11.sp)}
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
private fun DirectionKey(label: String,symbol: Symbol,onDown: () -> Unit,onUp: () -> Unit) {
    var pressed by remember { mutableStateOf(false) }
    val scope=rememberCoroutineScope()
    Surface(shape=RoundedCornerShape(17.dp),color=if(pressed)Color(0xFF49452F)else Raised,border=BorderStroke(1.dp,if(pressed)Accent else Line),
        modifier=Modifier.size(82.dp,60.dp)
            .semantics {
                role=Role.Button;contentDescription=label
                onClick { onDown();scope.launch { delay(180);onUp() };true }
            }.pointerInput(Unit) {
                detectTapGestures(onPress={
                    pressed=true;onDown()
                    try { tryAwaitRelease() } finally { pressed=false;onUp() }
                })
            }) {
        Column(horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.Center) {
            Glyph(symbol,Modifier.size(19.dp));Spacer(Modifier.height(3.dp));Text(label,fontSize=12.sp)
        }
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
