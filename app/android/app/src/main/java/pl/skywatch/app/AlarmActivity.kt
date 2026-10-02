package pl.skywatch.app

import android.app.Activity
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Alarm pełnoekranowy: widać go na zablokowanym ekranie, dźwięk idzie strumieniem alarmowym (jak budzik). */
class AlarmActivity : Activity() {

    companion object {
        private var player: MediaPlayer? = null

        fun intent(ctx: Context, id: Int, level: Int, text: String) = Intent(ctx, AlarmActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra("id", id).putExtra("level", level).putExtra("text", text)

        @Synchronized fun playSound(ctx: Context) {
            if (player != null) return
            val uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE) ?: return
            player = runCatching {
                MediaPlayer().apply {
                    setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                    setDataSource(ctx, uri); isLooping = true; prepare(); start()
                }
            }.getOrNull()
        }

        @Synchronized fun stopSound() { player?.runCatching { stop(); release() }; player = null }
    }

    private lateinit var root: View
    private lateinit var badge: TextView
    private lateinit var text: TextView
    private lateinit var clock: TextView
    private lateinit var mute: TextView
    private lateinit var pulse: Pulse
    private var alertId = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val col = vbox().apply { setPadding(dp(24), dp(20), dp(24), dp(24)) }
        root = col

        badge = text("", 13f, Color.WHITE, bold = true).apply {
            letterSpacing = 0.1f; setPadding(dp(14), dp(7), dp(14), dp(7)); background = rounded(0x33000000, 20)
        }
        col.add(badge, w = -2, top = dp(12))

        // środek: pulsujący sygnał + treść alarmu
        val mid = vbox().apply { gravity = Gravity.CENTER }
        pulse = Pulse(this).apply { color = Color.WHITE; active = true }
        mid.add(pulse, dp(120), dp(120))
        text = text("", 28f, Color.WHITE, bold = true).apply { gravity = Gravity.CENTER }
        mid.add(text, top = dp(24))
        clock = text("", 15f, 0xCCFFFFFF.toInt()).apply { gravity = Gravity.CENTER }
        mid.add(clock, top = dp(10))
        col.add(mid, h = 0, weight = 1f)

        col.add(text("Nie zastępuje oficjalnych ostrzeżeń (RCB, syreny).", 12f, 0xB3FFFFFF.toInt()).apply { gravity = Gravity.CENTER })
        col.add(pill("Potwierdzam — słyszę / widzę", Color.WHITE, 0xFF7A0010.toInt(), heightDp = 64, sizeSp = 18f) { feedback("confirm") }, top = dp(14))
        col.add(pill("To nie dron", 0, Color.WHITE, heightDp = 56) { feedback("false_alarm") }, top = dp(10))
        val low = hbox()
        mute = pill("🔇  Wycisz", 0x26000000, heightDp = 52, sizeSp = 15f) { stopSound(); refreshMute() }
        low.add(mute, 0, -2, weight = 1f)
        low.add(View(this), dp(10), 1)
        low.add(pill("Zamknij", 0x26000000, heightDp = 52, sizeSp = 15f) { stopSound(); finish() }, 0, -2, weight = 1f)
        col.add(low, top = dp(10))

        edgeToEdge(col)
        setContentView(col)
        show(intent)
    }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); show(intent) }

    private fun show(i: Intent) {
        alertId = i.getIntExtra("id", 0)
        val level = i.getIntExtra("level", 2)
        // poziom 1 (niepotwierdzone) — pomarańcz; 2–3 — czerwień
        val (c1, c2) = if (level >= 2) 0xFFD1242F.toInt() to 0xFF5E0A12.toInt() else 0xFFE07B00.toInt() to 0xFF5A2A00.toInt()
        root.background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, intArrayOf(c1, c2))
        @Suppress("DEPRECATION") run { window.statusBarColor = c1; window.navigationBarColor = c2 }
        badge.text = (if (alertId == 0) "TEST · " else "") + if (level >= 2) "ALARM · POZIOM $level" else "OSTRZEŻENIE · POZIOM 1"
        text.text = i.getStringExtra("text") ?: ""
        clock.text = SimpleDateFormat("HH:mm", Locale.US).format(Date())
        if (level >= 2) playSound(this)
        refreshMute()
    }

    private fun refreshMute() { mute.visibility = if (player != null) View.VISIBLE else View.INVISIBLE }

    private fun feedback(kind: String) {
        stopSound()
        getSystemService(NotificationManager::class.java).cancel(100)
        val net = WatchService.net ?: Net(this)
        Thread { net.event("feedback", JSONObject().put("alert", alertId).put("kind", kind)) }.start()
        finish()
    }
}
