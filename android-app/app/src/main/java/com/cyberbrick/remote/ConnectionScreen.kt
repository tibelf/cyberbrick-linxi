package com.cyberbrick.remote

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun ConnectionScreen(
    state: CloudState,server: String,username: String,password: String,rememberPassword: Boolean,
    onServer: (String) -> Unit,onUsername: (String) -> Unit,onPassword: (String) -> Unit,onRemember: (Boolean) -> Unit,
    onConnect: () -> Unit,onDisconnect: () -> Unit,onControl: () -> Unit,
) {
    var reveal by remember { mutableStateOf(false) }
    val busy=state.phase in listOf(LinkPhase.CONNECTING,LinkPhase.RECONNECTING)
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal=20.dp,vertical=8.dp),
        verticalArrangement=Arrangement.spacedBy(14.dp)) {
        Text("连接你的小车",fontSize=26.sp,fontWeight=FontWeight.SemiBold)
        Text("使用现有云端地址和控制密码。小车保持开机，并连接已配置的热点。",color=Muted,fontSize=12.sp,lineHeight=21.sp)
        OutlinedTextField(server,onServer,label={Text("云端地址")},singleLine=true,modifier=Modifier.fillMaxWidth(),
            enabled=!state.connected && !busy,shape=RoundedCornerShape(14.dp),
            keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Uri))
        OutlinedTextField(username,onUsername,label={Text("用户名")},singleLine=true,modifier=Modifier.fillMaxWidth(),
            enabled=!state.connected && !busy,shape=RoundedCornerShape(14.dp),
            keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Ascii))
        OutlinedTextField(password,onPassword,label={Text("控制密码")},singleLine=true,modifier=Modifier.fillMaxWidth(),
            enabled=!state.connected && !busy,shape=RoundedCornerShape(14.dp),
            keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Password),
            visualTransformation=if(reveal)VisualTransformation.None else PasswordVisualTransformation(),
            trailingIcon={TextButton(onClick={reveal=!reveal}){Text(if(reveal)"隐藏"else"显示",fontSize=11.sp)}})
        Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
            Checkbox(rememberPassword,onRemember)
            Text("记住密码，下次自动连接",fontSize=13.sp)
        }
        if(state.connected || busy) {
            OutlinedButton(onClick=onDisconnect,modifier=Modifier.fillMaxWidth().heightIn(min=52.dp),shape=RoundedCornerShape(15.dp)) {
                if(busy) {CircularProgressIndicator(Modifier.size(18.dp),strokeWidth=2.dp);Spacer(Modifier.width(10.dp))}
                Text(if(busy)"取消连接"else"断开连接")
            }
        } else Button(onClick=onConnect,modifier=Modifier.fillMaxWidth().heightIn(min=56.dp),shape=RoundedCornerShape(15.dp)) {
            Glyph(Symbol.LINK,color=Ground);Spacer(Modifier.width(8.dp));Text("连接云端",fontWeight=FontWeight.Bold)
        }
        Surface(color=Panel,shape=RoundedCornerShape(18.dp),border=BorderStroke(1.dp,Line),modifier=Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp),verticalArrangement=Arrangement.spacedBy(14.dp)) {
                SmallStatus(if(state.connected)"云端已连接" else if(busy)"云端连接中" else "云端未连接",state.connected)
                SmallStatus(if(state.carOnline)if(state.realCar)"真实小车在线"else"模拟小车在线"else"等待小车上线",state.carOnline)
                SmallStatus(if(state.linx=="connected" && state.audio!=null)"灵矽语音已就绪"else"灵矽语音未就绪",state.linx=="connected" && state.audio!=null)
                Text(state.message,fontSize=12.sp,color=Muted,lineHeight=20.sp)
            }
        }
        if(state.connected) Button(onClick=onControl,modifier=Modifier.fillMaxWidth().heightIn(min=48.dp),shape=RoundedCornerShape(15.dp)) {Text("进入遥控")}
        Text("同一时间只连接一个控制端。使用 App 前，请先断开原网页的连接。",fontSize=11.sp,color=Muted,lineHeight=19.sp)
        Text("麦克风仅在点击说话后开启；切到后台会结束收音、停止操作并锁定发射。",fontSize=11.sp,color=Muted,lineHeight=19.sp,modifier=Modifier.padding(bottom=12.dp))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FireControls(state: CloudState,vm: RemoteViewModel,onDismiss: () -> Unit) {
    ModalBottomSheet(onDismissRequest=onDismiss,containerColor=Panel,contentColor=MaterialTheme.colorScheme.onSurface) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal=24.dp).padding(bottom=24.dp),
            verticalArrangement=Arrangement.spacedBy(16.dp)) {
            Text("炮台与单发",fontSize=25.sp,fontWeight=FontWeight.SemiBold)
            Text("每次点击只微调一点。",fontSize=12.sp,color=Muted)
            Row(horizontalArrangement=Arrangement.spacedBy(10.dp)) {
                BarrelButton("抬高一点",Symbol.UP,{vm.cloud.barrel("up")},Modifier.weight(1f),state.canControl)
                BarrelButton("压低一点",Symbol.DOWN,{vm.cloud.barrel("down")},Modifier.weight(1f),state.canControl)
            }
            HorizontalDivider(color=Line)
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Glyph(if(state.armed)Symbol.UNLOCK else Symbol.LOCK,color=Accent)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(5.dp)) {
                    Text(if(state.armed)"发射已启用"else"发射已锁定",fontSize=16.sp,fontWeight=FontWeight.SemiBold)
                    Text(state.fireReason,fontSize=11.sp,color=Muted,lineHeight=19.sp)
                }
                Switch(checked=state.armed,onCheckedChange={vm.cloud.arm(it)},enabled=state.canControl && state.fireAvailable)
            }
            Text("确认朝向安全的靶区，再启用发射。每次点击一发，冷却结束后可再次操作。",fontSize=12.sp,color=Muted,lineHeight=21.sp)
            Button(onClick=vm.cloud::fire,enabled=state.canControl && state.armed && state.cooldownMs==0L,
                modifier=Modifier.fillMaxWidth().heightIn(min=58.dp),shape=RoundedCornerShape(16.dp)) {
                Glyph(Symbol.BOLT,color=if(state.armed)Ground else Muted);Spacer(Modifier.width(10.dp))
                Text(if(state.cooldownMs>0)"冷却 ${"%.1f".format(state.cooldownMs/1000.0)} 秒"else if(state.armed)"发射一发"else"请先启用发射",fontSize=16.sp,fontWeight=FontWeight.Bold)
            }
            StopButton(state.connected,vm::stop,Modifier.fillMaxWidth())
            Text("停车、断线或切到后台后，发射会重新锁定。",fontSize=11.sp,color=Muted,lineHeight=18.sp)
        }
    }
}
