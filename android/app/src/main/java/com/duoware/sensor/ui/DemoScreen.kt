package com.duoware.sensor.ui

import android.app.Activity
import android.graphics.Color
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView
import com.duoware.sensor.service.SensorCore
import java.util.Locale

/**
 * The demo screen: black, one line of small grey text (fps, link, thermal level), screen brightness 0.02, screen kept
 * on; a tap leaves it. Keeps the phone awake and in the foreground (required for the low-latency Wi-Fi lock) without
 * lighting up the arena.
 */
class DemoScreen(private val activity: Activity, private val core: () -> SensorCore?, private val onLeave: () -> Unit) {
    private val line = TextView(activity).apply {
        setTextColor(Color.rgb(90, 90, 90)); textSize = 11f; gravity = Gravity.CENTER
    }
    val root: View = android.widget.FrameLayout(activity).apply {
        setBackgroundColor(Color.BLACK)
        addView(line, android.widget.FrameLayout.LayoutParams(-1, -1))
        setOnClickListener { onLeave() }
    }

    fun show() {
        activity.window.attributes = activity.window.attributes.apply { screenBrightness = BRIGHTNESS }
        activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        activity.setContentView(root)
        refresh()
    }

    fun refresh() {
        val s = core()?.snapshot
        line.text = if (s == null) "starting" else String.format(Locale.ROOT, "%s fps  %s  thermal L%s",
            StatsView.num(s.fps), s.link?.mode ?: "no link", s.thermal?.level ?: "-")
    }

    companion object {
        const val BRIGHTNESS = 0.02f
    }
}
