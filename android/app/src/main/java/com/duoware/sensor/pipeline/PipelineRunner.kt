package com.duoware.sensor.pipeline

import android.media.Image
import android.os.Process

/**
 * The `pipeline` thread (M2 plan "Threads and the hot path"): takes the newest image from the [ImageMailbox], runs the
 * [FramePipeline] on its Y plane, closes the image and loops. It never waits on the network: the frame goes into the
 * [SendSlot] and the sender thread does the rest.
 */
class PipelineRunner(
    private val mailbox: ImageMailbox<Image>,
    private val pipeline: FramePipeline,
    private val onWork: ((workNs: Long) -> Unit)? = null,       // ADPF reportActualWorkDuration (step 10)
) {
    private var thread: Thread? = null

    /** Thread id of the pipeline thread (for the ADPF hint session), 0 until it runs. */
    @Volatile var tid = 0
        private set

    @Volatile private var running = false

    /** Frames the runner could not process (bad arguments from the native side). */
    @Volatile var rejected = 0L
        private set

    fun start() {
        if (running) return
        running = true
        thread = Thread({
            tid = Process.myTid()
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_DISPLAY)
            while (running) {
                val img = mailbox.take() ?: break
                val t0 = System.nanoTime()
                try {
                    val plane = img.planes[0]
                    if (pipeline.processFrame(plane.buffer, plane.rowStride, img.width, img.height, img.timestamp,
                            mailbox.availNs) < 0) rejected++
                } finally {
                    img.close()
                }
                onWork?.invoke(System.nanoTime() - t0)
            }
        }, "pipeline").also { it.start() }
    }

    fun stop() {
        running = false
        mailbox.close()
        thread?.join(JOIN_MS)
        thread = null
    }

    companion object {
        private const val JOIN_MS = 2000L
    }
}
