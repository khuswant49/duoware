package com.duoware.sensor.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import com.duoware.sensor.MainActivity
import com.duoware.sensor.R

/**
 * The foreground service (type camera) that keeps the sensor alive with the screen off or the app in the background,
 * holds a partial wake lock while it runs and owns the [SensorCore]. It must be started from the visible activity
 * (Android requires a camera foreground service to start from the foreground).
 */
class SensorService : Service() {
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            core?.stop()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, CHANNEL).setContentTitle(getString(R.string.app_name))
            .setContentText("Tracking markers").setSmallIcon(android.R.drawable.ic_menu_camera).setContentIntent(open)
            .setOngoing(true).build()
        startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA)
        if (wake == null) {
            wake = (getSystemService(Context.POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "duoware:sensor").apply { setReferenceCounted(false); acquire() }
        }
        val c = core ?: SensorCore(applicationContext).also { core = it }
        c.perf.wakeLock = true
        c.start()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        core?.perf?.wakeLock = false
        core?.stop()
        wake?.let { if (it.isHeld) it.release() }
        wake = null
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.duoware.sensor.STOP"
        private const val CHANNEL = "sensor"
        private const val ID = 1

        /** The one core of this process; the activity reads it for the stats. */
        @Volatile var core: SensorCore? = null

        fun start(ctx: Context) = ctx.startForegroundService(Intent(ctx, SensorService::class.java))
        fun stop(ctx: Context) = ctx.startService(Intent(ctx, SensorService::class.java).setAction(ACTION_STOP))
    }
}
