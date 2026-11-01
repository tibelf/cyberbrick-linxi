package com.cyberbrick.remote

import okhttp3.Credentials
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import okio.ByteString
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/** 本地可信测试 CA + 真 WSS；不连接生产服务器或真实小车。 */
class CloudClientTest {
    private val certificate = HeldCertificate.Builder().commonName("localhost")
        .addSubjectAlternativeName("localhost").build()
    private val server = MockWebServer()
    private val ticks = AtomicLong(1000)
    private lateinit var client: CloudClient
    private lateinit var config: ConnectionConfig

    private class Peer : WebSocketListener() {
        val messages = CopyOnWriteArrayList<JSONObject>()
        val audio = LinkedBlockingQueue<ByteString>()
        @Volatile var socket: WebSocket? = null
        override fun onOpen(webSocket: WebSocket,response: Response) { socket=webSocket }
        override fun onMessage(webSocket: WebSocket,text: String) {
            val message=JSONObject(text);messages+=message
            if(message.optString("type")=="auth") {
                send("""{"type":"ready","mode":"真实小车","car_online":true,"linx":"connected","fire":{"available":true,"armed":false,"reason":"发射已锁定"}}""")
                send("""{"type":"audio_format","format":"pcm","sample_rate":16000,"channels":1,"bit_depth":16}""")
                lease()
            }
        }
        override fun onMessage(webSocket: WebSocket,bytes: ByteString) { audio.offer(bytes) }
        override fun onClosing(webSocket: WebSocket,code: Int,reason: String) {webSocket.close(code,reason)}
        fun send(text: String) { check(socket!!.send(text)) }
        fun lease(id: String="current-lease") {send("""{"type":"lease","id":"$id"}""")}
        fun count(type: String)=messages.count {it.optString("type")==type}
        fun last(type: String)=messages.last {it.optString("type")==type}
    }

    @Before fun setup() {
        val serverTrust=HandshakeCertificates.Builder().heldCertificate(certificate).build()
        val clientTrust=HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()
        server.useHttps(serverTrust.sslSocketFactory(),false)
        server.start()
        val http=OkHttpClient.Builder().sslSocketFactory(clientTrust.sslSocketFactory(),clientTrust.trustManager)
            .readTimeout(0,TimeUnit.SECONDS).build()
        client=CloudClient(http, ticks::get)
        config=ConnectionConfig(server.url("/").toString(),"car","仅用于本地测试")
    }
    @After fun cleanup() {client.close();server.shutdown()}

    private fun await(message: String="等待条件超时",timeoutMs: Long=4000,condition: () -> Boolean) {
        val end=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        while(System.nanoTime()<end) {
            if(condition())return
            Thread.sleep(10)
        }
        assertTrue(message,condition())
    }
    private fun connect(): Peer {
        val peer=Peer()
        server.enqueue(MockResponse().withWebSocketUpgrade(peer))
        client.connect(config)
        await {client.state.value.canControl && client.state.value.audio!=null && peer.count("presence")>0}
        return peer
    }

    @Test fun `认证使用Basic和独立token并立即应答当前租约`() {
        val peer=connect()
        val request=server.takeRequest(2,TimeUnit.SECONDS)!!
        assertEquals("/control",request.path)
        assertEquals(Credentials.basic(config.username,config.password),request.getHeader("Authorization"))
        assertEquals(config.server.removeSuffix("/"),request.getHeader("Origin"))
        assertEquals(config.password,peer.last("auth").getString("token"))
        assertEquals("current-lease",peer.last("presence").getString("lease"))
        assertFalse(client.state.value.armed)
    }

    @Test fun `租约过期即停止手动驾驶且不接受新动作`() {
        val peer=connect()
        client.drive("forward")
        await {peer.count("drive")>0}
        assertEquals("current-lease",peer.last("drive").getString("lease"))
        ticks.addAndGet(901)
        await {peer.count("stop")>0}
        val before=peer.count("drive")
        client.drive("backward");client.barrel("up")
        Thread.sleep(180)
        assertEquals(before,peer.count("drive"))
        assertEquals(0,peer.count("barrel"))
        assertFalse(client.state.value.armed)
    }

    @Test fun `单发须云端确认启用且快速连点不排队`() {
        val peer=connect()
        client.fire()
        assertEquals(0,peer.count("fire"))
        client.arm(true)
        await {peer.count("fire_arm")==1}
        assertFalse(client.state.value.armed)
        peer.send("""{"type":"fire_state","available":true,"armed":true,"reason":"发射已启用"}""")
        await {client.state.value.armed}
        repeat(5) {client.fire()}
        await {peer.count("fire")==1}
        assertTrue(client.state.value.cooldownMs>0)
        client.stop()
        await {peer.count("stop")>0}
        assertFalse(client.state.value.armed)
        client.fire()
        Thread.sleep(100)
        assertEquals(1,peer.count("fire"))
    }

    @Test fun `原生音频上行固定20毫秒并在停止收音后恢复静音`() {
        val peer=connect()
        val silence=peer.audio.poll(2,TimeUnit.SECONDS)!!
        assertEquals(640,silence.size)
        assertTrue(silence.toByteArray().all {it==0.toByte()})
        assertTrue(client.microphone(true))
        val frame=ByteArray(640) {7}
        assertTrue(client.pcm(frame))
        await {peer.audio.any {bytes -> bytes.toByteArray().contentEquals(frame)}}
        assertTrue(client.microphone(false))
        assertFalse(client.pcm(frame))
        peer.audio.clear()
        assertTrue(peer.audio.poll(2,TimeUnit.SECONDS)!!.toByteArray().all {it==0.toByte()})
        assertThrows(IllegalArgumentException::class.java) {client.pcm(ByteArray(320))}
    }

    @Test fun `回复暂停收音且下行格式以协商为准`() {
        val peer=connect()
        var speaking=false
        var received: ByteArray?=null
        client.onSpeaking={speaking=true}
        client.onPcm={received=it}
        assertTrue(client.microphone(true))
        peer.send("""{"type":"linx_event","event":{"type":"speaking","on":true}}""")
        await {speaking && client.state.value.speaking}
        assertFalse(client.microphone(true))
        assertFalse(client.pcm(ByteArray(640)))
        peer.send("""{"type":"audio_format","format":"pcm","sample_rate":24000,"channels":1,"bit_depth":16}""")
        await {client.state.value.audio?.rate==24000}
        peer.socket!!.send(ByteString.of(1,2,3,4))
        await {received!=null}
        assertArrayEquals(byteArrayOf(1,2,3,4),received)
    }

    @Test fun `后台停止并锁定而前台重连不重放动作或收音`() {
        val first=connect()
        client.drive("forward")
        assertTrue(client.microphone(true))
        await {first.count("drive")>0}
        client.setForeground(false)
        await {first.count("stop")>0 && first.count("fire_arm")>0}
        assertFalse(first.last("fire_arm").getBoolean("on"))
        assertFalse(first.last("mic").getBoolean("on"))
        assertEquals(LinkPhase.DISCONNECTED,client.state.value.phase)
        val second=Peer()
        server.enqueue(MockResponse().withWebSocketUpgrade(second))
        client.setForeground(true)
        await {client.state.value.canControl && second.count("presence")>0}
        Thread.sleep(250)
        assertEquals(0,second.count("drive"))
        assertEquals(0,second.count("fire"))
        assertFalse(second.messages.any {it.optString("type")=="mic" && it.optBoolean("on")})
        assertFalse(client.state.value.armed)
    }

    @Test fun `已有控制者时不循环争抢连接`() {
        val peer=connect()
        peer.socket!!.close(4002,"另一个控制者在线")
        await {client.state.value.phase==LinkPhase.DISCONNECTED}
        assertTrue(client.state.value.error!!.contains("已有控制端"))
        Thread.sleep(1200)
        assertEquals(1,server.requestCount)
    }

    @Test fun `云端关闭连接后自动重连且丢弃原动作和发射启用`() {
        val first=connect()
        client.drive("forward")
        await {first.count("drive")>0}
        first.send("""{"type":"fire_state","available":true,"armed":true,"reason":"发射已启用"}""")
        await {client.state.value.armed}
        val second=Peer()
        server.enqueue(MockResponse().withWebSocketUpgrade(second))
        first.socket!!.close(1012,"服务重启")
        await {client.state.value.phase==LinkPhase.RECONNECTING}
        assertFalse(client.state.value.armed)
        await {client.state.value.canControl && second.count("presence")>0}
        Thread.sleep(250)
        assertEquals(0,second.count("drive"))
        assertEquals(0,second.count("fire"))
        assertFalse(client.state.value.armed)
    }

    @Test fun `生产客户端拒绝未知CA且不降低证书验证`() {
        client.close()
        client=CloudClient()
        server.enqueue(MockResponse().withWebSocketUpgrade(Peer()))
        client.connect(config)
        await {client.state.value.error?.contains("证书验证失败")==true}
        assertEquals(LinkPhase.DISCONNECTED,client.state.value.phase)
        assertFalse(client.state.value.connected)
    }

    @Test fun `连接信息拒绝明文或混入凭据的URL并隐藏密码`() {
        listOf("http://localhost","https://user:pass@localhost","https://localhost/control",
            "https://localhost/?token=secret","https://localhost/#secret").forEach {url ->
            assertThrows(IllegalArgumentException::class.java) {config.copy(server=url).endpoint()}
        }
        assertFalse(config.toString().contains(config.password))
        assertThrows(IllegalArgumentException::class.java) {config.copy(password="").endpoint()}
    }
}
