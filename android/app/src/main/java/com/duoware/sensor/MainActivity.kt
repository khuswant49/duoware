package com.duoware.sensor

import android.app.Activity
import android.os.Bundle
import android.view.Gravity
import android.view.WindowManager
import android.widget.TextView

/** M0 skeleton: an empty status screen. The marker sensor is built in milestone M2. */
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(TextView(this).apply {
            text = "DUO-WARE Sensor ${BuildConfig.VERSION_NAME} (protocol v${BuildConfig.PROTOCOL_VERSION})\nNot implemented yet (M2)."
            textSize = 20f
            gravity = Gravity.CENTER
        })
    }
}
