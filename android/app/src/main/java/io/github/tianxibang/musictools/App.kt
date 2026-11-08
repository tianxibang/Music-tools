package io.github.tianxibang.musictools

import android.app.Application
import io.github.tianxibang.musictools.sync.SyncWorker

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        SyncWorker.createChannel(this)
        SyncWorker.schedule(this)
    }
}
