package io.github.tianxibang.musictools.sync

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Environment
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.tianxibang.musictools.R
import io.github.tianxibang.musictools.data.Prefs
import kotlinx.coroutines.sync.Mutex
import java.util.concurrent.TimeUnit

class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    private val log = SyncLog.get(context)

    override suspend fun doWork(): Result {
        val prefs = Prefs.get(applicationContext)
        if (prefs.pc == null) return Result.success()
        // The periodic and the "sync now" work are separate; never let them run at the same time
        if (!running.tryLock()) return Result.success()
        try {
            return sync(prefs)
        } finally {
            running.unlock()
        }
    }

    private suspend fun sync(prefs: Prefs): Result {
        if (Build.VERSION.SDK_INT >= 30 && !Environment.isExternalStorageManager()) {
            log.finish(null, "Music Tools needs access to your files. Open the app to allow it.")
            return Result.success()
        }
        runCatching { setForeground(foregroundInfo("Syncing with ${prefs.pc?.name}…", 0, 0)) }
        log.start()
        var lastNotified = 0L
        log.onChange = { s ->
            val now = System.currentTimeMillis()
            if (now - lastNotified > 500) {
                lastNotified = now
                runCatching { setForegroundAsync(foregroundInfo(s.status, s.step, s.steps)) }
            }
        }
        return try {
            val manual = inputData.getBoolean(KEY_MANUAL, false)
            val result = SyncEngine(applicationContext, log, retryFailed = manual).run()
            log.finish(result.summary(), null)
            Result.success()
        } catch (e: kotlinx.coroutines.CancellationException) {
            log.finish(null, "Sync was stopped")
            throw e
        } catch (e: Exception) {
            log.line(e.message ?: e.toString(), Level.ERROR)
            log.finish(null, e.message ?: e.toString())
            // Unreachable PC is normal away from home; the periodic sync tries again later
            Result.success()
        } finally {
            log.onChange = null
        }
    }

    private fun foregroundInfo(text: String, step: Int, steps: Int): ForegroundInfo {
        val n: Notification = NotificationCompat.Builder(applicationContext, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Syncing music")
            .setContentText(text)
            .setOngoing(true)
            .setSilent(true)
            .setProgress(steps, step, steps == 0)
            .build()
        return if (Build.VERSION.SDK_INT >= 29) {
            ForegroundInfo(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            ForegroundInfo(NOTIFICATION_ID, n)
        }
    }

    companion object {
        private val running = Mutex()
        private const val KEY_MANUAL = "manual"
        const val CHANNEL = "sync"
        private const val NOTIFICATION_ID = 1
        private const val ONE_TIME = "sync-now"
        private const val PERIODIC = "sync-periodic"

        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Sync progress", NotificationManager.IMPORTANCE_LOW)
            )
        }

        private fun constraints(context: Context) = Constraints.Builder()
            .setRequiredNetworkType(
                if (Prefs.get(context).settings.wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED
            ).build()

        fun syncNow(context: Context) {
            val req = OneTimeWorkRequestBuilder<SyncWorker>()
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .setInputData(workDataOf(KEY_MANUAL to true))
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(ONE_TIME, ExistingWorkPolicy.KEEP, req)
        }

        fun stop(context: Context) = WorkManager.getInstance(context).cancelUniqueWork(ONE_TIME)

        /** Every 30 minutes while on the right kind of network; Android picks the exact moment. */
        fun schedule(context: Context) {
            val wm = WorkManager.getInstance(context)
            val prefs = Prefs.get(context)
            if (!prefs.settings.autoSync || prefs.pc == null) {
                wm.cancelUniqueWork(PERIODIC)
                return
            }
            val req = PeriodicWorkRequestBuilder<SyncWorker>(30, TimeUnit.MINUTES)
                .setConstraints(constraints(context))
                .build()
            wm.enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.UPDATE, req)
        }
    }
}
