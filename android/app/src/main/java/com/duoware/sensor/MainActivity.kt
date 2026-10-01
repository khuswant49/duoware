package com.duoware.sensor

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import com.duoware.sensor.service.SensorCore
import com.duoware.sensor.service.SensorService
import com.duoware.sensor.store.Prefs
import com.duoware.sensor.ui.BenchmarkView
import com.duoware.sensor.ui.DemoScreen
import com.duoware.sensor.ui.StatsView

/**
 * The control screen (plain Views): mode WIRED / WIRELESS, camera, Start / Stop, Setup / Tracking, Refocus, pair code,
 * manual host, and the stats at 2 Hz. The screen stays on in every mode (FLAG_KEEP_SCREEN_ON). The service is started
 * from here, while the activity is visible, because Android requires that of a camera foreground service.
 */
class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var stats: StatsView
    private lateinit var bench: BenchmarkView
    private lateinit var camera: Spinner
    private lateinit var pair: EditText
    private lateinit var manual: EditText
    private lateinit var modeGroup: RadioGroup
    private var demo: DemoScreen? = null
    private var inDemo = false
    private val core: SensorCore? get() = SensorService.core
    private lateinit var prefs: Prefs

    private val refresh = object : Runnable {
        override fun run() {
            core?.let { c ->
                if (inDemo) demo?.refresh() else { stats.update(c); bench.update(c) }
            } ?: run { if (!inDemo) stats.text = "stopped" }
            ui.postDelayed(this, REFRESH_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        prefs = Prefs(getSharedPreferences("duoware", MODE_PRIVATE))
        if (sustainedSupported()) window.setSustainedPerformanceMode(true)
        setContentView(buildUi())
        requestPermissions()
        debugAutomation(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        debugAutomation(intent)
    }

    override fun onResume() {
        super.onResume()
        core?.perf?.sustainedMode = sustainedSupported()      // reported only when the window really asked for it
        ui.post(refresh)
    }

    override fun onPause() {
        ui.removeCallbacks(refresh)
        super.onPause()
    }

    private fun sustainedSupported() =
        (getSystemService(POWER_SERVICE) as PowerManager).isSustainedPerformanceModeSupported

    private fun requestPermissions() {
        val need = ArrayList<String>()
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) need += Manifest.permission.CAMERA
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            need += Manifest.permission.POST_NOTIFICATIONS
        }
        if (need.isNotEmpty()) requestPermissions(need.toTypedArray(), REQ_PERMISSIONS)
    }

    private fun buildUi(): View {
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 16, 24, 16) }
        col.addView(TextView(this).apply {
            text = "DUO-WARE Sensor ${BuildConfig.VERSION_NAME} (protocol v${BuildConfig.PROTOCOL_VERSION})"
            textSize = 16f
        })
        modeGroup = RadioGroup(this).apply { orientation = RadioGroup.HORIZONTAL }
        val wired = RadioButton(this).apply { text = "WIRED"; id = View.generateViewId() }
        val wireless = RadioButton(this).apply { text = "WIRELESS"; id = View.generateViewId() }
        modeGroup.addView(wired); modeGroup.addView(wireless)
        modeGroup.check(if (prefs.mode == Prefs.WIRELESS) wireless.id else wired.id)
        modeGroup.setOnCheckedChangeListener { _, id ->
            val m = if (id == wireless.id) Prefs.WIRELESS else Prefs.WIRED
            if (m != prefs.mode) { prefs.mode = m; core?.restart() }
        }
        col.addView(modeGroup)

        camera = Spinner(this)
        val choices = try { com.duoware.sensor.camera.Capabilities.choices(this) } catch (e: Exception) { emptyList() }
        camera.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, choices.map { it.label })
        choices.indexOfFirst { it.id == prefs.cameraId }.takeIf { it >= 0 }?.let { camera.setSelection(it) }
        camera.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: android.widget.AdapterView<*>?, v: View?, pos: Int, id: Long) {
                val c = choices.getOrNull(pos) ?: return
                if (c.id != prefs.cameraId) { prefs.cameraId = c.id; core?.restart() }
            }
            override fun onNothingSelected(p: android.widget.AdapterView<*>?) {}
        }
        col.addView(camera)

        val row1 = LinearLayout(this)
        row1.addView(button("Start") { start() }); row1.addView(button("Stop") { SensorService.stop(this) })
        row1.addView(button("Setup/Tracking") { toggleMode(it as Button) })
        row1.addView(button("Refocus") { core?.refocus() })
        col.addView(row1)
        val row2 = LinearLayout(this)
        row2.addView(button("Demo screen") { enterDemo() })
        row2.addView(button("Battery") { batteryHelp() })
        row2.addView(button("Benchmark") { toggleBenchmark(it as Button) })
        col.addView(row2)

        pair = EditText(this).apply { hint = "pair code (when asked)"; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS; setSingleLine() }
        col.addView(pair)
        col.addView(button("Pair") { core?.restart(pair.text.toString().trim().ifEmpty { null }) })
        manual = EditText(this).apply { hint = "manual host (advanced)"; setText(prefs.manualHost ?: ""); setSingleLine() }
        col.addView(manual)
        col.addView(button("Save manual host") { prefs.manualHost = manual.text.toString(); core?.restart() })

        stats = StatsView(this)
        col.addView(stats)
        bench = BenchmarkView(this)
        col.addView(bench)
        return ScrollView(this).apply { addView(col) }
    }

    private fun button(label: String, onClick: (View) -> Unit) = Button(this).apply {
        text = label; setOnClickListener(onClick)
    }

    private fun start() {
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) { requestPermissions(); return }
        SensorService.start(this)
    }

    private fun toggleBenchmark(b: Button) {
        val c = core ?: return
        if (c.benchRunning) { c.cancelBenchmark(); return }
        c.startBenchmark()?.let { AlertDialog.Builder(this).setMessage("Cannot start: $it").setPositiveButton("OK", null).show() }
    }

    /**
     * Debug builds only (adb): `--ez autostart true` starts the sensor, `--ei bench_measure_s N [--ei bench_warm_s M]` runs a
     * benchmark with shortened durations once the sensor streams. Release builds ignore these extras; the defaults in
     * the code stay 5 s warm-up and 30 s measured.
     */
    private fun debugAutomation(i: Intent?) {
        if (!BuildConfig.DEBUG || i == null) return
        if (i.getBooleanExtra("autostart", false)) start()
        val measure = i.getIntExtra("bench_measure_s", 0)
        if (measure > 0) {
            val warm = i.getIntExtra("bench_warm_s", 1)
            ui.postDelayed(object : Runnable {
                var tries = 0
                override fun run() {
                    val c = core
                    val err = if (c == null) "no core" else c.startBenchmark(warm, measure)
                    if (err != null && ++tries < DEBUG_BENCH_TRIES) ui.postDelayed(this, 1000)
                }
            }, 3000)
        }
    }

    private fun toggleMode(b: Button) {
        val c = core ?: return
        c.appMode = if (c.appMode == "tracking") "setup" else "tracking"
        b.text = "Mode: ${c.appMode}"
    }

    private fun enterDemo() {
        val d = DemoScreen(this, { core }, { leaveDemo() })
        demo = d; inDemo = true
        d.show()
    }

    private fun leaveDemo() {
        inDemo = false
        window.attributes = window.attributes.apply { screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE }
        recreate()
    }

    /** The realme steps (D34): without them ColorOS kills background apps. */
    private fun batteryHelp() {
        val exempt = (getSystemService(POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(packageName)
        AlertDialog.Builder(this).setTitle("Keep the sensor alive")
            .setMessage("Battery optimisation exempt: $exempt\n\nOn the realme: Settings > Battery > More battery settings > " +
                "Optimise battery use > DUO-WARE Sensor > Don't optimise. Settings > Apps > DUO-WARE Sensor > Battery usage > " +
                "Allow background activity. Lock the app in the recents list. Keep the phone charging.")
            .setPositiveButton("Ask Android") { _, _ ->
                if (!exempt) startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
            }.setNegativeButton("Close", null).show()
    }

    override fun onBackPressed() {
        if (inDemo) leaveDemo() else super.onBackPressed()
    }

    companion object {
        private const val REQ_PERMISSIONS = 1
        private const val DEBUG_BENCH_TRIES = 40
        private const val REFRESH_MS = 500L              // stats at 2 Hz
    }
}
