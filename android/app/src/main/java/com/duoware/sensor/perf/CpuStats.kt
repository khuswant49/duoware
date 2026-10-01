package com.duoware.sensor.perf

import android.system.Os
import android.system.OsConstants
import java.io.File

/**
 * `status.cpu` (PROTOCOL.md §4.5), sampled once a second: `app_pct` = the app's CPU time (`/proc/self/stat`
 * utime + stime) / wall time / cores x 100, and `max_cur_khz` = the current frequency of the fastest core, `null` when
 * the kernel does not let the app read it (never a guess).
 */
class CpuStats(private val nowNs: () -> Long, private val readStat: () -> String? = { readSelfStat() }) {
    val cores: Int = Runtime.getRuntime().availableProcessors()
    private val ticksPerSec: Long = try { Os.sysconf(OsConstants._SC_CLK_TCK).takeIf { it > 0 } ?: 100L } catch (e: Throwable) { 100L }
    private var lastTicks = -1L
    private var lastNs = 0L
    private val maxKhz: List<Long> = (0 until cores).map { readLong("/sys/devices/system/cpu/cpu$it/cpufreq/cpuinfo_max_freq") ?: 0L }
    private val fastest: Int = maxKhz.indices.maxByOrNull { maxKhz[it] } ?: 0

    /** `cpuinfo_max_freq` of every core that reports one (the `hello` `cpu.max_khz`); empty when none is readable. */
    fun maxKhzReadable(): List<Long> = maxKhz.filter { it > 0 }

    /** App CPU % since the previous call, `null` for the first call or when `/proc/self/stat` is unreadable. */
    fun appPct(): Double? {
        val ticks = parseTicks(readStat() ?: return null) ?: return null
        val now = nowNs()
        val pct = if (lastTicks < 0) null else percent(ticks - lastTicks, now - lastNs, cores, ticksPerSec)
        lastTicks = ticks; lastNs = now
        return pct
    }

    fun maxCurKhz(): Long? = readLong("/sys/devices/system/cpu/cpu$fastest/cpufreq/scaling_cur_freq")

    companion object {
        /** utime + stime (fields 14 and 15) of a `/proc/<pid>/stat` line; the command name may hold spaces and `)`. */
        fun parseTicks(line: String): Long? {
            val rest = line.substringAfterLast(')', "").trim().split(' ')
            // after the command: state is field 3, so utime and stime are rest[11] and rest[12]
            if (rest.size < 13) return null
            val u = rest[11].toLongOrNull() ?: return null
            val s = rest[12].toLongOrNull() ?: return null
            return u + s
        }

        fun percent(deltaTicks: Long, deltaWallNs: Long, cores: Int, ticksPerSec: Long): Double? {
            if (deltaTicks < 0 || deltaWallNs <= 0 || cores <= 0 || ticksPerSec <= 0) return null
            val cpuS = deltaTicks.toDouble() / ticksPerSec
            return cpuS / (deltaWallNs / 1e9) / cores * 100.0
        }

        private fun readSelfStat(): String? = try { File("/proc/self/stat").readText() } catch (e: Exception) { null }

        private fun readLong(path: String): Long? = try { File(path).readText().trim().toLongOrNull() } catch (e: Exception) { null }
    }
}
