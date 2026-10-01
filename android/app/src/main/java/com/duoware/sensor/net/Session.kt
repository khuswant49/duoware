package com.duoware.sensor.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiInfo as AndroidWifiInfo
import com.duoware.sensor.proto.CameraCaps
import com.duoware.sensor.proto.CameraSettings
import com.duoware.sensor.proto.CpuInfo
import com.duoware.sensor.proto.Hello
import com.duoware.sensor.proto.LinkInfo
import com.duoware.sensor.proto.Messages
import com.duoware.sensor.proto.ProtocolException
import com.duoware.sensor.proto.ServerError
import com.duoware.sensor.proto.Welcome
import com.duoware.sensor.proto.WifiInfo
import com.duoware.sensor.store.Prefs
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * The phone's WebSocket session (PROTOCOL.md §4.3): `hello` on open, then `welcome` / `settings` / `error` from the
 * server and `status` / `bench` / `preview` to it. A closed socket, for any reason, ends the session ([Listener.onClosed]):
 * the owner closes the frames transport and starts discovery again after 1 s (a new session, DECISIONS.md D35).
 */
class Session(private val prefs: Prefs, private val listener: Listener) {
    interface Listener {
        fun onWelcome(w: Welcome)
        fun onSettings(s: CameraSettings)
        /** `bad_token` has already cleared the stored token; the UI asks for the pair code. */
        fun onServerError(e: ServerError)
        fun onVersionMismatch(serverVersion: Int)
        fun onClosed(reason: String)
    }

    private var socket: WebSocket? = null
    @Volatile private var open = false
    @Volatile private var ended = false

    /** Opens the WebSocket to [found] and sends [hello]; the hello is built by the caller (see [helloFor]). */
    @Synchronized
    fun open(found: Found, hello: Hello) {
        val client = OkHttpClient.Builder()
            .socketFactory(NetworkBinder(found.route).socketFactory())
            .pingInterval(PING_S, TimeUnit.SECONDS)
            .connectTimeout(CONNECT_S, TimeUnit.SECONDS)
            .build()
        val req = Request.Builder().url("ws://${found.host}:${found.beacon.httpPort}/ws/phone").build()
        ended = false
        socket = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                open = true
                webSocket.send(Messages.hello(hello))
            }

            override fun onMessage(webSocket: WebSocket, text: String) = handle(text)

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = finish("closed $code $reason")

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                finish("failed: ${t.message}")
        })
    }

    private fun handle(text: String) {
        try {
            val o = Messages.parseObject(text)
            when (Messages.type(o)) {
                "welcome" -> {
                    val w = Messages.welcome(o)
                    w.token?.let { prefs.token = it }
                    listener.onWelcome(w)
                }
                "settings" -> listener.onSettings(Messages.settings(o))
                "error" -> {
                    val e = Messages.error(o)
                    if (e.code == "bad_token") prefs.token = null
                    listener.onServerError(e)
                }
                else -> Unit                                    // unknown types are ignored (PROTOCOL.md §0)
            }
        } catch (e: ProtocolException) {
            val v = e.message?.removePrefix("server speaks protocol v")?.toIntOrNull()
            if (v != null) listener.onVersionMismatch(v)
        }
    }

    /** Sends a text message; false when the socket is not open or its queue is full. */
    fun send(text: String): Boolean = open && socket?.send(text) == true

    /** Bytes OkHttp has queued but not written (the preview skips sending above 64 KB, plan "Threads"). */
    fun queueSize(): Long = socket?.queueSize() ?: 0L

    fun sendBinary(bytes: okio.ByteString): Boolean = open && socket?.send(bytes) == true

    @Synchronized
    fun close() {
        socket?.close(NORMAL_CLOSE, "bye")
        socket?.cancel()
        socket = null
        open = false
    }

    private fun finish(reason: String) {
        open = false
        if (ended) return
        ended = true
        listener.onClosed(reason)
    }

    companion object {
        private const val PING_S = 5L
        private const val CONNECT_S = 3L
        private const val NORMAL_CLOSE = 1000

        /**
         * The `hello` of PROTOCOL.md §4.3. `pair_code` only without a stored token (or after `bad_token`, which clears
         * it); [pairCode] is what the user typed, if anything.
         */
        fun helloFor(
            ctx: Context, prefs: Prefs, found: Found, caps: CameraCaps, cpu: CpuInfo, appVersion: String, pairCode: String?,
        ): Hello {
            val token = prefs.token
            return Hello(
                prefs.deviceId, token, if (token == null) pairCode?.ifBlank { null } else null, appVersion,
                android.os.Build.MODEL, android.os.Build.VERSION.RELEASE, android.os.Build.VERSION.SDK_INT,
                linkInfo(ctx, found), cpu, caps,
            )
        }

        fun linkInfo(ctx: Context, found: Found): LinkInfo {
            val net = found.network
            var wifi: WifiInfo? = null
            if (net != null) {
                val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
                val caps = cm.getNetworkCapabilities(net)
                val info = caps?.transportInfo as? AndroidWifiInfo
                if (info != null) {
                    val mhz = info.frequency
                    val band = if (mhz <= 0) null else if (mhz > 5900) 6.0 else if (mhz > 4900) 5.0 else 2.4
                    wifi = WifiInfo(band, info.rssi.takeIf { it > -127 }, info.linkSpeed.takeIf { it > 0 })
                } else if (caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true) {
                    wifi = WifiInfo(null, null, null)
                }
            }
            return LinkInfo(found.linkMode, found.iface, found.localIp, wifi)
        }
    }
}
