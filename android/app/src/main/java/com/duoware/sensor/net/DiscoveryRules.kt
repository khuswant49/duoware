package com.duoware.sensor.net

import com.duoware.sensor.proto.Beacon

/** Pure discovery rules (PROTOCOL.md §4.1, §4.2): interface classes, subnet match, server choice. */
object DiscoveryRules {
    private val TETHER = listOf("rndis", "usb", "ncm")               // USB tethering interfaces
    private val PHONE_HOTSPOT = listOf("ap", "swlan", "softap")       // the phone's own Wi-Fi hotspot
    private val WIFI_CLIENT = listOf("wlan")

    fun isTether(name: String) = TETHER.any { name.startsWith(it) }
    fun isPhoneHotspot(name: String) = PHONE_HOTSPOT.any { name.startsWith(it) }
    fun isWifiClient(name: String) = WIFI_CLIENT.any { name.startsWith(it) }

    /** Dotted IPv4 to an Int (big-endian), or null. */
    fun ipv4(text: String): Int? {
        val parts = text.split('.')
        if (parts.size != 4) return null
        var v = 0
        for (p in parts) {
            val n = p.toIntOrNull() ?: return null
            if (n !in 0..255 || p.isEmpty() || (p.length > 1 && p[0] == '0')) return null
            v = (v shl 8) or n
        }
        return v
    }

    fun inSubnet(host: String, ifaceAddress: String, prefixLength: Int): Boolean {
        val h = ipv4(host) ?: return false
        val a = ipv4(ifaceAddress) ?: return false
        if (prefixLength !in 0..32) return false
        val mask = if (prefixLength == 0) 0 else (-1 shl (32 - prefixLength))
        return (h and mask) == (a and mask)
    }

    /** Only beacons whose host is on the chosen interface's subnet are acceptable (§4.1). */
    fun acceptable(b: Beacon, ifaceAddress: String, prefixLength: Int) = inSubnet(b.host, ifaceAddress, prefixLength)

    /**
     * The server to connect to among acceptable beacons: one per server_id (the first heard), the stored pairing's
     * server preferred, else the first heard.
     */
    fun choose(heard: List<Beacon>, storedServerId: String?): Beacon? {
        val unique = heard.distinctBy { it.serverId }
        return unique.firstOrNull { it.serverId == storedServerId } ?: unique.firstOrNull()
    }
}
