package pl.skywatch.app

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Aktualizacje z serwera grupy, bez ponownej instalacji z pliku.
 *
 * Serwer mówi, jaka wersja jest aktualna (/api/update); aplikacja pobiera APK i instaluje je przez
 * PackageInstaller. Za pierwszym razem Android pyta o zgodę („instalowanie nieznanych aplikacji”
 * i „Aktualizuj”). Potem aplikacja jest już instalatorem samej siebie i od Androida 12 kolejne
 * aktualizacje mogą przejść bez pytania.
 */
object Updater {
    private const val ACTION = "pl.skywatch.app.INSTALL_STATUS"
    @Volatile var available: String? = null
    @Volatile var status: String? = null

    /** Zwraca nazwę nowszej wersji albo null. */
    fun check(ctx: Context): String? {
        // bez dołączania i podpisu: aktualizacja musi dojść także do telefonu, którego serwer odrzuca (tak naprawiamy takie błędy)
        val r = Net(ctx).plainGet("/api/update?flavor=${BuildConfig.CHANNEL}")
        val newer = r.optInt("versionCode") > BuildConfig.VERSION_CODE
        available = if (newer) r.optString("versionName") else null
        return available
    }

    fun notifyAvailable(ctx: Context, version: String) {
        val pi = PendingIntent.getActivity(ctx, 7, Intent(ctx, MainActivity::class.java).putExtra("update", true),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        ctx.getSystemService(NotificationManager::class.java).notify(200,
            Notification.Builder(ctx, WatchService.CH_QUIETEST).setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("${ctx.getString(R.string.app_name)}: aktualizacja $version").setContentText("Dotknij, aby zainstalować")
                .setContentIntent(pi).setAutoCancel(true).build())
    }

    /** Pobiera i instaluje. Wywoływać poza wątkiem UI. */
    fun install(ctx: Context) {
        val prefs = Prefs(ctx)
        status = "pobieram…"
        val f = File(ctx.cacheDir, "update.apk")
        val path = "/api/apk?flavor=${BuildConfig.CHANNEL}"
        val c = URL(prefs.url + path).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 5000; c.readTimeout = 60000
            c.setRequestProperty("Authorization", "Bearer ${prefs.token}")
            // podpis kluczem urządzenia (Updater.check przed chwilą przeszedł przez Net, więc punkt jest już dołączony)
            prefs.keyNode?.takeIf { prefs.joined == it }?.let { n -> Keys.headers("GET", path, null, n).forEach { (k, v) -> c.setRequestProperty(k, v) } }
            if (c.responseCode != 200) throw RuntimeException("serwer ${c.responseCode}")
            c.inputStream.use { i -> f.outputStream().use { i.copyTo(it) } }
        } finally { c.disconnect() }

        status = "instaluję…"
        val pi = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(ctx.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = pi.createSession(params)
        pi.openSession(id).use { s ->
            s.openWrite("skywatch.apk", 0, f.length()).use { out -> f.inputStream().use { it.copyTo(out) }; s.fsync(out) }
            // jawny odbiorca — odbiornik w manifeście nie ma filtra, więc samo ACTION+pakiet nigdy by nie doszło
            val cb = PendingIntent.getBroadcast(ctx, id, Intent(ctx, StatusReceiver::class.java).setAction(ACTION),
                PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            s.commit(cb.intentSender)
        }
    }

    class StatusReceiver : BroadcastReceiver() {
        override fun onReceive(ctx: Context, i: Intent) {
            when (val st = i.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    status = "potwierdź instalację"
                    @Suppress("DEPRECATION")
                    (i.getParcelableExtra<Intent>(Intent.EXTRA_INTENT))?.let { confirm ->
                        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        // z tła system może zablokować otwarcie okna — wtedy powiadomienie do dotknięcia
                        if (runCatching { ctx.startActivity(confirm) }.isFailure) {
                            val pi = PendingIntent.getActivity(ctx, 8, confirm, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
                            ctx.getSystemService(NotificationManager::class.java).notify(201,
                                Notification.Builder(ctx, WatchService.CH_QUIETEST).setSmallIcon(android.R.drawable.stat_sys_download_done)
                                    .setContentTitle("SkywatchPL: potwierdź aktualizację").setContentText("Dotknij, aby zainstalować")
                                    .setContentIntent(pi).setAutoCancel(true).build())
                        }
                    }
                }
                PackageInstaller.STATUS_SUCCESS -> status = "zainstalowano"
                else -> status = "błąd instalacji ($st): ${i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}"
            }
        }
    }

    /** Po aktualizacji system zatrzymuje aplikację — wstań z powrotem, jeśli czuwanie było włączone. */
    class Restart : BroadcastReceiver() {
        override fun onReceive(ctx: Context, i: Intent) {
            if (Prefs(ctx).running) runCatching { ctx.startForegroundService(Intent(ctx, WatchService::class.java)) }
        }
    }
}
