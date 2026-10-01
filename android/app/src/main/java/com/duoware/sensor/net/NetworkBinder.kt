package com.duoware.sensor.net

import android.net.Network
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import javax.net.SocketFactory

/** Where sockets must go (PROTOCOL.md §4.1): nothing uses the default route, so mobile data never carries frames. */
sealed interface Route {
    /** The phone is a Wi-Fi client: bind to that `Network`. */
    class Wifi(val network: Network) : Route

    /** Tether or the phone's own hotspot: bind to the interface's address. */
    class Iface(val address: InetAddress) : Route

    /** `adb reverse`: the server is on the phone's loopback. */
    object Loopback : Route
}

/** Binds sockets (UDP, TCP, OkHttp's) to a [Route] before they connect. */
class NetworkBinder(val route: Route) {
    /** Binds an unconnected socket (`DatagramSocket` or `Socket` of a channel). */
    fun bind(socket: Socket) {
        when (val r = route) {
            is Route.Wifi -> r.network.bindSocket(socket)
            is Route.Iface -> socket.bind(InetSocketAddress(r.address, 0))
            Route.Loopback -> Unit
        }
    }

    fun bind(socket: java.net.DatagramSocket) {
        when (val r = route) {
            is Route.Wifi -> r.network.bindSocket(socket)
            is Route.Iface -> socket.bind(InetSocketAddress(r.address, 0))
            Route.Loopback -> Unit
        }
    }

    /** For OkHttp (HTTP probe and WebSocket): every socket is bound before it connects. */
    fun socketFactory(): SocketFactory = when (val r = route) {
        is Route.Wifi -> r.network.socketFactory
        else -> object : SocketFactory() {
            override fun createSocket(): Socket = Socket().also { bind(it) }
            override fun createSocket(host: String, port: Int): Socket =
                createSocket().also { it.connect(InetSocketAddress(host, port)) }
            override fun createSocket(host: String, port: Int, localHost: InetAddress, localPort: Int): Socket =
                createSocket(host, port)
            override fun createSocket(host: InetAddress, port: Int): Socket =
                createSocket().also { it.connect(InetSocketAddress(host, port)) }
            override fun createSocket(address: InetAddress, port: Int, localAddress: InetAddress, localPort: Int): Socket =
                createSocket(address, port)
        }
    }
}
