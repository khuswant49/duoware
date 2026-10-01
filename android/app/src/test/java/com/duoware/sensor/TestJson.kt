package com.duoware.sensor

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.fail

/** Test helpers: fixtures from src/test/resources, and a JSON comparison that treats 5 and 5.0 as equal. */
object TestJson {
    fun resource(path: String): String =
        TestJson::class.java.classLoader!!.getResourceAsStream(path)?.bufferedReader(Charsets.UTF_8)?.readText()
            ?: error("missing test resource $path")

    fun resourceBytes(path: String): ByteArray =
        TestJson::class.java.classLoader!!.getResourceAsStream(path)?.readBytes() ?: error("missing $path")

    /** A PROTOCOL.md fixture without its `_comment` key. */
    fun fixture(name: String): JSONObject = JSONObject(resource("protocol/$name.json")).also { it.remove("_comment") }

    fun assertSameJson(expected: Any?, actual: Any?, path: String = "$") {
        when {
            isNull(expected) || isNull(actual) ->
                if (!(isNull(expected) && isNull(actual))) fail("$path: expected $expected, got $actual")
            expected is Number && actual is Number ->
                assertEquals("$path", expected.toDouble(), actual.toDouble(), 1e-9)
            expected is JSONObject && actual is JSONObject -> {
                val keys = expected.keys().asSequence().toSortedSet()
                assertEquals("$path keys", keys, actual.keys().asSequence().toSortedSet())
                for (k in keys) assertSameJson(expected.get(k), actual.get(k), "$path.$k")
            }
            expected is JSONArray && actual is JSONArray -> {
                assertEquals("$path length", expected.length(), actual.length())
                for (i in 0 until expected.length()) assertSameJson(expected.get(i), actual.get(i), "$path[$i]")
            }
            else -> assertEquals(path, expected, actual)
        }
    }

    private fun isNull(v: Any?) = v == null || v == JSONObject.NULL
}
