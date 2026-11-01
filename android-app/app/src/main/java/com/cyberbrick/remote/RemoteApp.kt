package com.cyberbrick.remote

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun RemoteApp(vm: RemoteViewModel) {
    val cloud by vm.cloud.state.collectAsStateWithLifecycle()
    val audio by vm.audio.state.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(2) }
    var fireSheet by remember { mutableStateOf(false) }
    var server by rememberSaveable { mutableStateOf(vm.initialSettings.first.server) }
    var username by rememberSaveable { mutableStateOf(vm.initialSettings.first.username) }
    // 密码不进入 savedInstanceState，记住密码仅走 Keystore。
    var password by remember { mutableStateOf(vm.initialSettings.first.password) }
    var rememberPassword by rememberSaveable { mutableStateOf(vm.initialSettings.second) }
    val context = LocalContext.current
    val focus = LocalFocusManager.current
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { allowed ->
        if (allowed) vm.audio.startRecording()
        else vm.cloud.report("麦克风权限被拒绝，可在系统设置中允许后重试")
    }
    val talk: () -> Unit = {
        when {
            audio.recording || audio.preparing -> vm.audio.stopRecording()
            !cloud.canControl || cloud.audio == null -> vm.cloud.report("请先连接小车与灵矽语音")
            cloud.speaking || audio.playing -> vm.cloud.report("请等小车回复播完后再说话")
            context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED -> vm.audio.startRecording()
            else -> permission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
    LaunchedEffect(cloud.connected) {
        if (cloud.connected) { focus.clearFocus(); tab = 0 }
    }
    BackHandler(enabled = tab != 0 && !fireSheet) { focus.clearFocus(); tab = 0 }

    Scaffold(
        modifier = Modifier.fillMaxSize().imePadding(),
        containerColor = Ground,
        contentWindowInsets = WindowInsets(0),
        topBar = {
            Row(Modifier.fillMaxWidth().statusBarsPadding().height(68.dp).padding(horizontal = 20.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                BrandMark()
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text("CyberBrick",fontSize=20.sp,fontWeight=FontWeight.Bold)
                    Text(listOf("云端遥控","语音控车","连接设置")[tab],fontSize=11.sp,color=Muted)
                }
                if(tab != 2) OutlinedIconButton(onClick={tab=2},shape=RoundedCornerShape(14.dp),
                    modifier=Modifier.size(44.dp).semantics { contentDescription="连接设置" },border=BorderStroke(1.dp,Line)) {
                    Glyph(Symbol.SETTINGS)
                }
            }
        },
        bottomBar = {
            Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal=20.dp)) {
                if(tab != 2) StopButton(cloud.connected,vm::stop,Modifier.fillMaxWidth().padding(top=8.dp,bottom=8.dp))
                NavigationBar(containerColor=Ground,windowInsets=WindowInsets(0),modifier=Modifier.height(70.dp)) {
                    listOf("遥控" to Symbol.GAMEPAD,"语音" to Symbol.MIC,"连接" to Symbol.LINK).forEachIndexed { index,item ->
                        NavigationBarItem(selected=tab==index,onClick={focus.clearFocus();tab=index},
                            icon={Glyph(item.second,color=if(tab==index)Accent else Muted)},
                            label={Text(item.first,fontSize=11.sp)},
                            colors=NavigationBarItemDefaults.colors(selectedIconColor=Accent,selectedTextColor=Accent,
                                unselectedIconColor=Muted,unselectedTextColor=Muted,indicatorColor=Color(0xFF302E23)))
                    }
                }
            }
        }
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if(cloud.error != null) Surface(color=StopBackground,shape=RoundedCornerShape(14.dp),
                modifier=Modifier.fillMaxWidth().padding(horizontal=20.dp,vertical=6.dp)) {
                Row(Modifier.padding(start=12.dp),verticalAlignment=Alignment.CenterVertically) {
                    Text(cloud.error ?: "",Modifier.weight(1f),color=StopInk,fontSize=12.sp,lineHeight=19.sp)
                    IconButton(onClick=vm.cloud::clearError,modifier=Modifier.semantics { contentDescription="关闭提示" }) { Glyph(Symbol.CLOSE,color=StopInk) }
                }
            }
            when(tab) {
                0 -> if(cloud.canControl) ControlScreen(cloud,vm.cloud,
                    onVoice={tab=1;talk()},onFireDetails={fireSheet=true})
                else OfflineScreen(cloud,onSettings={tab=2},onRetry={
                    if (cloud.connected) vm.cloud.report("云端已连接，正在等待小车上线，请检查小车电源和热点")
                    else vm.connect(ConnectionConfig(server,username,password),rememberPassword)
                })
                1 -> VoiceScreen(cloud,audio,talk)
                2 -> ConnectionScreen(cloud,server,username,password,rememberPassword,
                    onServer={server=it},onUsername={username=it},onPassword={password=it},onRemember={rememberPassword=it;if(!it)vm.forgetPassword()},
                    onConnect={focus.clearFocus();vm.connect(ConnectionConfig(server,username,password),rememberPassword)},
                    onDisconnect={vm.audio.stopRecording();vm.audio.flush();vm.cloud.disconnect()},onControl={tab=0})
            }
        }
    }
    if(fireSheet) FireControls(cloud,vm,onDismiss={fireSheet=false})
}

@Composable
fun StopButton(enabled: Boolean,onStop: () -> Unit,modifier: Modifier=Modifier) {
    Button(onClick=onStop,enabled=enabled,modifier=modifier.heightIn(min=52.dp),shape=RoundedCornerShape(15.dp),
        border=BorderStroke(1.dp,if(enabled)Color(0xFF82534F)else Line),
        colors=ButtonDefaults.buttonColors(containerColor=StopBackground,contentColor=StopInk,
            disabledContainerColor=Raised,disabledContentColor=Muted)) {
        Glyph(Symbol.STOP,color=if(enabled)StopInk else Muted)
        Spacer(Modifier.width(9.dp));Text("立即停止并锁定",fontSize=15.sp,fontWeight=FontWeight.SemiBold)
    }
}

@Composable
fun SmallStatus(label: String,ready: Boolean) {
    Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(6.dp)) {
        Glyph(if(ready)Symbol.CHECK else Symbol.LINK,Modifier.size(16.dp),if(ready)Accent else Muted)
        Text(label,fontSize=12.sp,color=if(ready)MaterialTheme.colorScheme.onSurface else Muted)
    }
}
