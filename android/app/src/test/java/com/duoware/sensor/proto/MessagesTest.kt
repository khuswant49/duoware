package com.duoware.sensor.proto

import com.duoware.sensor.TestJson
import com.duoware.sensor.TestJson.assertSameJson
import com.duoware.sensor.TestJson.fixture
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** Encoders and parsers against the PROTOCOL.md examples (copied fixtures; the server's A7 test guards the source). */
class MessagesTest {
    private val hello = Hello(
        "5b1e0f5e-8a8c-4f53-9b0e-2f4c1f2a6c11", null, "K7QX-M2", "0.2.0", "realme RMX3393", "13", 33,
        LinkInfo("wireless", "wlan0", "192.168.1.23", WifiInfo(5.0, -51, 866)),
        CpuInfo(8, List(6) { 2000000L } + List(2) { 2500000L }),
        CameraCaps(
            "0", "FULL", listOf("MANUAL_SENSOR", "READ_SENSOR_SETTINGS", "BURST_CAPTURE"), "REALTIME",
            10000L to 500000000L, 100 to 6400, listOf(15 to 30, 30 to 30, 60 to 60),
            listOf(YuvSize(1920, 1080, 30.0), YuvSize(1280, 720, 60.0), YuvSize(640, 480, 120.0)), true, 10.0,
            listOf(1402.1, 1402.1, 640.3, 361.0, 0.0), listOf(0.021, -0.043, 0.0, 0.0, 0.0), 4080 to 3072,
        ),
    )

    private val status = Status(
        1, "tracking", "boottime", LinkInfo("wireless", "wlan0", "192.168.1.23", WifiInfo(5.0, -53, 780)),
        59.7, 60.0, 1280 to 720,
        mapOf("pipeline" to Pct(28.0, 34.1), "detect_full" to Pct(14.2, 17.9), "detect_roi" to Pct(1.9, 2.8),
            "send" to Pct(0.3, 0.6), "cap_to_sent" to Pct(33.5, 41.0)),
        10, 2.0, 3, 3, 0, 3000000, 800, "locked", 23.5, 2400000, ThermalState(0, 0.42, 0, null),
        PerfState(true, true, true, false, true, true), 81, true, 7, null,
    )

    @Test
    fun helloMatchesTheProtocolExample() = assertSameJson(fixture("hello"), JSONObject(Messages.hello(hello)))

    @Test
    fun statusMatchesTheProtocolExample() = assertSameJson(fixture("status"), JSONObject(Messages.status(status)))

    @Test
    fun statusCarriesProcessingOnOnlyWhenReported() {
        assertTrue(JSONObject(Messages.status(status.copy(processingOn = listOf("ois")))).has("processing_on"))
        assertTrue(!JSONObject(Messages.status(status)).has("processing_on"))
    }

    @Test
    fun unmeasuredValuesAreExplicitNulls() {
        val o = JSONObject(Messages.status(status.copy(cpuAppPct = null, thermal = null)))
        assertTrue(o.getJSONObject("cpu").isNull("app_pct"))
        assertTrue(o.has("thermal") && o.isNull("thermal"))
    }

    @Test
    fun benchMatchesTheProtocolExample() {
        val r = BenchResult(1280 to 720, 0.0, "native_roi", 10, false, 30.0, 59.6, Pct(33.1, 40.2), Pct(14.0, 17.5),
            Pct(1.8, 2.6), 22.0, 0.40, 0.47, 11.0)
        assertSameJson(fixture("bench"), JSONObject(Messages.bench(Bench(1, "20261001-140200", listOf(r)))))
    }

    @Test
    fun syncReplyMatchesTheProtocolExample() = assertSameJson(
        fixture("sync_r"),
        JSONObject(Messages.syncReply(1, "9f2c4e1a7b3d5c60", 123, 5512345678901, 81234000000000, 81234000040000)),
    )

    @Test
    fun parsesSettingsWelcomeErrorAndSync() {
        val s = Messages.settings(fixture("settings"))
        assertEquals(1280 to 720, s.resolution)
        assertEquals(listOf(1, 5), s.tracking.trackIds)
        assertEquals("subpix", s.tracking.cornerRefine)
        assertEquals(listOf(960 to 540), s.thermal.resolutionSteps)
        val w = Messages.welcome(fixture("welcome"))
        assertEquals("f0c1".repeat(8), w.token)
        assertEquals(47802, w.framesTcpPort)
        assertEquals(s, w.settings)
        assertNull(Messages.welcome(fixture("welcome").put("token", JSONObject.NULL)).token)
        assertEquals("bad_pair_code", Messages.error(fixture("error")).code)
        assertEquals(SyncRequest("9f2c4e1a7b3d5c60", 123, 5512345678901), Messages.sync(fixture("sync")))
    }

    @Test
    fun badOrForeignMessagesAreProtocolErrors() {
        assertThrows(ProtocolException::class.java) { Messages.type(fixture("sync").put("v", 2)) }
        assertThrows(ProtocolException::class.java) { Messages.parseObject("not json") }
        assertThrows(ProtocolException::class.java) {
            Messages.settings(fixture("settings").also { it.getJSONObject("tracking").remove("aruco3") })
        }
        assertEquals("sync", Messages.type(fixture("sync")))
    }

    @Test
    fun beaconParsing() {
        val ok = BeaconParser.parse(TestJson.resource("protocol/beacon.json")) as BeaconParse.Ok
        assertEquals(Beacon("a3f9c2d17e804b55", "DUO-WARE", "192.168.42.129", 8000, 47801, 47802), ok.beacon)
        assertEquals(BeaconParse.WrongVersion(2), BeaconParser.parse(fixture("beacon").put("v", 2).toString()))
        assertTrue(BeaconParser.parse("{}") is BeaconParse.Bad)
        assertTrue(BeaconParser.parse(fixture("beacon").also { it.remove("host") }.toString()) is BeaconParse.Bad)
    }
}
