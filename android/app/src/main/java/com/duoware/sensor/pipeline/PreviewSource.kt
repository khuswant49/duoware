package com.duoware.sensor.pipeline

import java.nio.ByteBuffer

/**
 * What the pipeline needs from the preview (PROTOCOL.md §4.6): a buffer for the downscaled Y copy only when a preview is
 * due and the preview thread is idle, then a call saying the buffer holds a [publish]ed image (or [abort] if it does not).
 */
interface PreviewSource {
    /** Width of the downscaled copy to ask for (valid while [acquire] returned a buffer). */
    val width: Int

    /** A free buffer when a preview is due at [nowNs] (sync clock) and the encoder is idle, else null. */
    fun acquire(nowNs: Long): ByteBuffer?

    /** The buffer from [acquire] holds a [w] x [h] greyscale image captured at [capNs]. */
    fun publish(w: Int, h: Int, capNs: Long)

    /** The buffer from [acquire] was not filled. */
    fun abort()
}
