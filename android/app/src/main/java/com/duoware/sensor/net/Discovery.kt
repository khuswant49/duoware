package com.duoware.sensor.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.util.Log
import com.duoware.sensor.proto.Beacon
import com.duoware.sensor.proto.BeaconParse
import com.duoware.sensor.proto.BeaconParser
import com.duoware.sensor.store.Prefs
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

/** A server the phone can talk to, and the route to it (PROTOCOL.md §4.1). */
class Found(
    val beacon: Beacon,
    val route: Route,
    val linkMode: String,              // wired_tether | wired_adb | wireless
    val iface: String,
    val localIp: String,
    val network: Network?,             // the Wi-Fi network, for band/RSSI/link speed
) {
    /** The address to connect to: loopback for `wired_adb` whatever the beacon says. */
    val host: String get() = if (route === Route.Loopback) "127.0.0.1" else beacon.host
}

/**
 * Finds the server without typing an IP. WIRED: tether interface beacon (3 s), else the `adb reverse` probe on
 * loopback; WIRELESS: Wi-Fi client beacon (3 s, with a MulticastLock), else the gateway probe, or the phone's own
 * hotspot treated like a tether; a manual host is tried last in both. Repeats every 5 s until something answers.
 * [onStep] says which step runs (shown in the UI).
 */
class Discovery(
    private val ctx: Context,
    private val prefs: Prefs,
    private val onStep: (String) -> Unit,
    private val onFound: (Found) -> Unit,
    private val onVersionMismatch: (Int) -> Unit,
) {
    private val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    private val wifi = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    @Volatile private var running = false
    private var thread: Thread? = null
    @Volatile private var delayMs = 0L

    /** Starts the search loop; [firstDelayMs] is 1000 after a closed session (PROTOCOL.md §4.3 "discovery again after 1 s"). */
    @Synchronized
    fun start(firstDelayMs: Long = 0) {
        if (running) return
        running = true
        delayMs = firstDelayMs
        thread = Thread({ loop() }, "discovery").also { it.isDaemon = true; it.start() }
    }

    @Synchronized
    fun stop() {
        running = false
        thread?.interrupt()
        thread?.join(JOIN_MS)
        thread = null
    }

    private fun loop() {
        try {
            sleep(delayMs)
            while (running) {
                val found = try {
                    search()
                } catch (e: Exception) {
                    Log.w(TAG, "discovery step failed", e); null
                }
                if (found != null && running) {
                    running = false                       // the session owns the lifecycle now; the service restarts us
                    onFound(found)
                    return
                }
                sleep(RETRY_MS)
            }
        } catch (_: InterruptedException) {
            // stopped
        }
    }

    private fun sleep(ms: Long) {
        if (ms > 0) Thread.sleep(ms)
    }

    private fun search(): Found? {
        val manual = prefs.manualHost
        return if (prefs.mode == Prefs.WIRELESS) wireless(manual) else wired(manual)
    }

    // ------------------------------------------------------------------------------------------ WIRED

    private fun wired(manual: String?): Found? {
        val tether = interfaces().firstOrNull { DiscoveryRules.isTether(it.name) }
        if (tether != null) {
            onStep("Listening for the server beacon on ${tether.name}")
            val route = Route.Iface(tether.address)
            listen(tether.address.hostAddress!!, tether.prefix)?.let {
                return Found(it, route, "wired_tether", tether.name, tether.address.hostAddress!!, null)
            }
        }
        onStep("Probing adb reverse on 127.0.0.1:$DEFAULT_HTTP_PORT")
        probe("127.0.0.1", DEFAULT_HTTP_PORT, NetworkBinder(Route.Loopback))?.let {
            return Found(it, Route.Loopback, "wired_adb", "lo", "127.0.0.1", null)
        }
        if (manual != null) {
            onStep("Trying the manual host $manual")
            if (tether != null) {
                probe(manual, DEFAULT_HTTP_PORT, NetworkBinder(Route.Iface(tether.address)))?.let {
                    return Found(it, Route.Iface(tether.address), "wired_tether", tether.name, tether.address.hostAddress!!, null)
                }
            }
        }
        return null
    }

    // ---------------------------------------------------------------------------------------- WIRELESS

    private fun wireless(manual: String?): Found? {
        val net = wifiClient()
        if (net != null) {
            val lp = cm.getLinkProperties(net)
            val addr = lp?.ipv4Address()
            if (lp != null && addr != null) {
                val name = lp.interfaceName ?: "wlan0"
                val route = Route.Wifi(net)
                val local = addr.address.hostAddress!!
                val lock = wifi.createMulticastLock("duoware-beacon").apply { setReferenceCounted(false) }
                lock.acquire()
                try {
                    onStep("Listening for the server beacon on Wi-Fi ($name)")
                    listen(local, addr.prefixLength)?.let { return Found(it, route, "wireless", name, local, net) }
                } finally {
                    if (lock.isHeld) lock.release()
                }
                lp.routes.firstOrNull { it.isDefaultRoute && it.gateway is Inet4Address }?.gateway?.hostAddress?.let { gw ->
                    onStep("Probing the Wi-Fi gateway $gw")
                    probe(gw, DEFAULT_HTTP_PORT, NetworkBinder(route))?.let {
                        return Found(it, route, "wireless", name, local, net)
                    }
                }
                if (manual != null) {
                    onStep("Trying the manual host $manual")
                    probe(manual, DEFAULT_HTTP_PORT, NetworkBinder(route))?.let {
                        return Found(it, route, "wireless", name, local, net)
                    }
                }
                return null
            }
        }
        val hotspot = interfaces().firstOrNull { DiscoveryRules.isPhoneHotspot(it.name) }
        if (hotspot != null) {                                        // the phone is the hotspot: same as a tether
            onStep("Listening for the server beacon on the hotspot ${hotspot.name}")
            val route = Route.Iface(hotspot.address)
            val local = hotspot.address.hostAddress!!
            listen(local, hotspot.prefix)?.let { return Found(it, route, "wireless", hotspot.name, local, null) }
            if (manual != null) {
                onStep("Trying the manual host $manual")
                probe(manual, DEFAULT_HTTP_PORT, NetworkBinder(route))?.let {
                    return Found(it, route, "wireless", hotspot.name, local, null)
                }
            }
            return null
        }
        onStep("No Wi-Fi network: join the router or hotspot, or switch to WIRED")
        return null
    }

    // ------------------------------------------------------------------------------------------ pieces

    private class Iface(val name: String, val address: InetAddress, val prefix: Int)

    private fun interfaces(): List<Iface> = try {
        NetworkInterface.getNetworkInterfaces().toList().filter { it.isUp && !it.isLoopback }.flatMap { ni ->
            ni.interfaceAddresses.filter { it.address is Inet4Address }
                .map { Iface(ni.name, it.address, it.networkPrefixLength.toInt()) }
        }
    } catch (e: Exception) {
        emptyList()
    }

    private fun wifiClient(): Network? = cm.allNetworks.firstOrNull {
        cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
    }

    private fun LinkProperties.ipv4Address() = linkAddresses.firstOrNull { it.address is Inet4Address }

    /** Listens [BEACON_LISTEN_MS] on the beacon port; accepts only beacons on the interface's subnet (§4.1). */
    private fun listen(ifaceAddress: String, prefix: Int): Beacon? {
        val heard = ArrayList<Beacon>()
        val deadline = System.nanoTime() + BEACON_LISTEN_MS * 1_000_000L
        try {
            DatagramSocket(null).use { s ->
                s.reuseAddress = true
                s.broadcast = true
                s.bind(java.net.InetSocketAddress(BEACON_PORT))
                val buf = ByteArray(2048)
                while (running) {
                    val leftMs = (deadline - System.nanoTime()) / 1_000_000L
                    if (leftMs <= 0) break
                    s.soTimeout = leftMs.toInt()
                    val pkt = DatagramPacket(buf, buf.size)
                    try {
                        s.receive(pkt)
                    } catch (e: SocketTimeoutException) {
                        break
                    }
                    when (val p = BeaconParser.parse(pkt.data, 0, pkt.length)) {
                        is BeaconParse.Ok -> if (DiscoveryRules.acceptable(p.beacon, ifaceAddress, prefix)) {
                            heard += p.beacon
                            if (p.beacon.serverId == prefs.serverId) return p.beacon      // the paired server: no need to wait
                        }
                        is BeaconParse.WrongVersion -> onVersionMismatch(p.v)
                        is BeaconParse.Bad -> Unit
                    }
                }
            }
        } catch (e: java.io.IOException) {
            Log.w(TAG, "beacon listen failed: ${e.message}")
        }
        return DiscoveryRules.choose(heard, prefs.serverId)
    }

    /** `GET http://host:port/api/beacon` over the bound route (1 s timeouts). */
    private fun probe(host: String, port: Int, binder: NetworkBinder): Beacon? = try {
        val client = OkHttpClient.Builder().socketFactory(binder.socketFactory())
            .connectTimeout(PROBE_MS, TimeUnit.MILLISECONDS).readTimeout(PROBE_MS, TimeUnit.MILLISECONDS)
            .callTimeout(PROBE_MS * 2, TimeUnit.MILLISECONDS).build()
        client.newCall(Request.Builder().url("http://$host:$port/api/beacon").build()).execute().use { r ->
            if (!r.isSuccessful) null else when (val p = BeaconParser.parse(r.body?.string() ?: "")) {
                is BeaconParse.Ok -> p.beacon
                is BeaconParse.WrongVersion -> { onVersionMismatch(p.v); null }
                is BeaconParse.Bad -> null
            }
        }
    } catch (e: Exception) {
        null
    }

    companion object {
        private const val TAG = "Discovery"
        const val BEACON_PORT = 47800             // PROTOCOL.md §4.2
        const val DEFAULT_HTTP_PORT = 8000        // PROTOCOL.md §4.1: the configured port before any server was found
        private const val BEACON_LISTEN_MS = 3000L
        private const val RETRY_MS = 5000L
        private const val PROBE_MS = 1000L
        private const val JOIN_MS = 3000L
    }
}
