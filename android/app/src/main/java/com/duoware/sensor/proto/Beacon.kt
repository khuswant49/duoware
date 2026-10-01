package com.duoware.sensor.proto

import org.json.JSONObject

/** PROTOCOL.md §4.2 discovery beacon. */
data class Beacon(
    val serverId: String, val name: String, val host: String, val httpPort: Int, val framesPort: Int,
    val framesTcpPort: Int,
)

sealed interface BeaconParse {
    data class Ok(val beacon: Beacon) : BeaconParse
    data class WrongVersion(val v: Int) : BeaconParse     // shown as "server speaks protocol vN"
    data class Bad(val why: String) : BeaconParse
}

object BeaconParser {
    fun parse(text: String): BeaconParse {
        val o = try {
            JSONObject(text)
        } catch (e: Exception) {
            return BeaconParse.Bad("not JSON")
        }
        if (o.optString("t") != "beacon") return BeaconParse.Bad("not a beacon")
        val v = o.optInt("v", -1)
        if (v != PROTOCOL_VERSION) return BeaconParse.WrongVersion(v)
        return try {
            BeaconParse.Ok(Beacon(o.getString("server_id"), o.getString("name"), o.getString("host"),
                o.getInt("http_port"), o.getInt("frames_port"), o.getInt("frames_tcp_port")))
        } catch (e: Exception) {
            BeaconParse.Bad("missing field: ${e.message}")
        }
    }

    fun parse(bytes: ByteArray, offset: Int, length: Int): BeaconParse =
        parse(String(bytes, offset, length, Charsets.UTF_8))
}
