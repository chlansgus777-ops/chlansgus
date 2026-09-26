package com.chlansgus.ktx

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat

/**
 * 자동예약이 도는 동안 앱이 백그라운드로 가도 프로세스가 유지되도록 하는 포그라운드 서비스.
 * 실제 작업은 [BookingController]가 하며, 이 서비스는 알림 표시와 CPU 유지만 담당합니다.
 */
class AutoReserveService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        createChannels(this)
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0
        ServiceCompat.startForeground(this, ONGOING_ID, ongoing(this, "자동예약 진행 중"), type)
        if (wakeLock == null) {
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "KtxEasy:auto")
                .apply { acquire(6 * 60 * 60 * 1000L) }
        }
        instance = this
        stopIfIdle()
        return START_NOT_STICKY
    }

    /** Android 15의 dataSync 실행시간 제한에 도달하면 자동예약을 중지합니다. */
    override fun onTimeout(startId: Int, fgsType: Int) {
        BookingController.cancel()
        stopSelf()
    }

    private fun stopIfIdle() {
        if (!BookingController.state.value.auto) stopSelf()
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ONGOING = "auto"
        private const val CHANNEL_RESULT = "result"
        private const val ONGOING_ID = 1
        private const val RESULT_ID = 2

        /** startForeground를 마친 실행 중 인스턴스. 메인 스레드에서만 접근합니다. */
        private var instance: AutoReserveService? = null

        fun start(context: Context) =
            ContextCompat.startForegroundService(context, Intent(context, AutoReserveService::class.java))

        /**
         * startForeground 전에 stopService로 끝내면 Android 12+에서 앱이 강제 종료되므로,
         * 아직 시작 중이면 onStartCommand가 상태를 보고 스스로 멈추게 합니다.
         */
        fun stop() {
            Handler(Looper.getMainLooper()).post { instance?.stopIfIdle() }
        }

        fun update(context: Context, text: String) = notify(context, ONGOING_ID, ongoing(context, text))

        fun notifyResult(context: Context, title: String, text: String) {
            createChannels(context)
            val n = NotificationCompat.Builder(context, CHANNEL_RESULT)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(openApp(context))
                .setAutoCancel(true)
                .build()
            notify(context, RESULT_ID, n)
        }

        private fun notify(context: Context, id: Int, n: Notification) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) return
            context.getSystemService(NotificationManager::class.java).notify(id, n)
        }

        private fun ongoing(context: Context, text: String): Notification {
            createChannels(context)
            return NotificationCompat.Builder(context, CHANNEL_ONGOING)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("KTX 자동예약 진행 중")
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(openApp(context))
                .build()
        }

        private fun openApp(context: Context) = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        private fun createChannels(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ONGOING, "자동예약 진행", NotificationManager.IMPORTANCE_LOW))
            nm.createNotificationChannel(NotificationChannel(CHANNEL_RESULT, "예약 결과", NotificationManager.IMPORTANCE_HIGH))
        }
    }
}
