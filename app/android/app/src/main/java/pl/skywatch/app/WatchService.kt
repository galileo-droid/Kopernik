package pl.skywatch.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.LocationManager
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.BatteryManager
import android.os.IBinder
import android.os.PowerManager
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Usługa pierwszoplanowa: nasłuch mikrofonu (opcjonalnie), puls „żyję” i odbiór alarmów.
 *
 * Nasłuch MVP nie ma jeszcze modelu. Mierzy głośność (po filtrze górnoprzepustowym 80 Hz, żeby
 * wiatr mniej przeszkadzał) względem śledzonego szumu tła. Gdy przez 2 s jest o [Prefs.thresholdDb]
 * głośniej niż tło, wysyła wykrycie (same liczby, bez dźwięku). Nagranie (5 s wstecz, razem do 15 s)
 * powstaje tylko po wyzwoleniu tonem podobnym do Shaheda i przed zapisem przechodzi przez filtr
 * dolnoprzepustowy 600 Hz: harmoniczne silnika zostają, mowa staje się niezrozumiała.
 * Wersja dev (telefony koordynatora, do kalibracji) zapisuje dodatkowo: co sekundę poziom, widmo
 * i grzebień harmonicznych ([DevLog]), nagrania po głośności (najwyżej co 2 min, do 15 s) i dłuższe
 * nagrania po tonie (20 s wstecz, do 60 s) — wszystko wysyła na serwer; nagrania w 8 kHz.
 */
class WatchService : Service() {

    companion object {
        const val RATE = 16000
        const val CH_WATCH = "watch"
        const val CH_INFO = "info"
        const val CH_ALARM = "alarm"
        const val CH_QUIET = "quiet"
        /** Informacje (np. aktualizacja): w czujniku bez dźwięku, w aplikacji Alarm zwykłe. */
        val CH_QUIETEST get() = if (BuildConfig.SENSOR) CH_WATCH else CH_INFO

        // stan dla ekranu głównego (ten sam proces)
        @Volatile var running = false
        @Volatile var listening = false
        @Volatile var source = "-"
        @Volatile var levelDb = -120f
        @Volatile var floorDb = -120f
        @Volatile var lastDetection = 0L
        @Volatile var recordingClip = false
        @Volatile var harmF0 = 0f
        @Volatile var harmRun = 0
        @Volatile var harmPres = 0
        @Volatile var harmBg = false
        @Volatile var trackF0 = 0f
        @Volatile var trackSec = 0
        @Volatile var net: Net? = null
        @Volatile var bgLocation = false   // czuwanie może brać pozycję w tle
        @Volatile var degraded = false     // działa z mniejszymi możliwościami, niż pozwalają uprawnienia

        /** Prywatny katalog aplikacji (od 0.14; wcześniej katalog zewnętrzny — jego zawartość kasujemy). */
        fun clipsDir(ctx: Context): File {
            ctx.getExternalFilesDir(null)?.let { File(it, "clips") }?.takeIf { it.exists() }?.deleteRecursively()
            return File(ctx.filesDir, "clips").apply { mkdirs() }
        }

        fun channels(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH_WATCH, "Czuwanie", NotificationManager.IMPORTANCE_LOW))
            if (BuildConfig.SENSOR) {   // czujnik nie ma żadnego głośnego kanału (do 0.17 miał — kasujemy)
                listOf(CH_INFO, CH_QUIET, CH_ALARM).forEach { nm.deleteNotificationChannel(it) }
                return
            }
            nm.createNotificationChannel(NotificationChannel(CH_INFO, "Sygnały niepotwierdzone", NotificationManager.IMPORTANCE_HIGH))
            nm.createNotificationChannel(NotificationChannel(CH_QUIET, "Podejrzenia (bez dźwięku)", NotificationManager.IMPORTANCE_LOW))
            // dźwięk alarmu gra AlarmActivity (strumień alarmowy), kanał tylko pokazuje ekran
            nm.createNotificationChannel(NotificationChannel(CH_ALARM, "Alarm", NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(null, null); enableVibration(true)
            })
        }
    }

    private lateinit var prefs: Prefs
    private var wake: PowerManager.WakeLock? = null
    private val threads = mutableListOf<Thread>()
    @Volatile private var stop = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (running) {
            // wystartowane z tła (np. po aktualizacji) z okrojonymi uprawnieniami — teraz,
            // z widocznej aplikacji, można uruchomić pełne czuwanie
            if (!degraded) return START_STICKY
            teardown()
        }
        prefs = Prefs(this)
        channels(this)
        net = Net(this)
        val mic = prefs.sending && (prefs.listen || !BuildConfig.DEV) && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        // typ „location” pozwala czuwaniu brać pozycję w tle (Android 14+), ale tylko przy starcie
        // z widocznej aplikacji — przy starcie z tła próbujemy kolejno skromniejszych wariantów
        val loc = if (prefs.sending && Loc.any(this)) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION else 0
        val su = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        val mt = ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        val variants = buildList {
            if (mic) { if (loc != 0) add(Triple(mt or loc or su, true, true)); add(Triple(mt or su, true, false)) }
            if (loc != 0) add(Triple(loc or su, false, true))
            add(Triple(su, false, false))
        }
        for ((type, m, l) in variants) {
            val text = when {
                !BuildConfig.SENSOR -> "Czekam na alarmy"
                m -> "Czujnik działa"
                else -> "Czujnik bez mikrofonu — otwórz aplikację"
            }
            if (runCatching { startForeground(1, watchNotification(text), type) }.isSuccess) {
                bgLocation = l
                degraded = (mic && !m) || (loc != 0 && !l)
                return startLoops(m)
            }
        }
        stopSelf(); return START_NOT_STICKY
    }


    private fun startLoops(mic: Boolean): Int {
        running = true; stop = false
        wake = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "skywatch:watch").apply { acquire() }
        if (mic) threads += Thread(::audioLoop, "audio")
        // czujnik nie odbiera alarmów: nie może zdradzić się dźwiękiem ani nagrać własnej syreny
        if (!BuildConfig.SENSOR) threads += Thread(::alertLoop, "alerts")
        threads += Thread(::heartbeatLoop, "heartbeat")
        threads.forEach { it.start() }
        return START_STICKY
    }

    override fun onDestroy() { teardown(); super.onDestroy() }

    private fun teardown() {
        stop = true; running = false; listening = false
        threads.forEach { it.interrupt() }
        threads.forEach { runCatching { it.join(3000) } }   // mikrofon musi się zwolnić przed ponownym startem
        threads.clear()
        wake?.takeIf { it.isHeld }?.release()
    }

    private fun watchNotification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CH_WATCH)
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    // ---------------------------------------------------------------- nasłuch

    @SuppressLint("MissingPermission")
    private fun audioLoop() {
        val am = getSystemService(AudioManager::class.java)
        // Część telefonów (np. Samsungi) nie deklaruje UNPROCESSED, a mimo to je obsługuje — próbujemy zawsze.
        val declared = am.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED) == "true"
        val minBuf = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        fun open(src: Int) = runCatching {
            AudioRecord(src, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(minBuf, RATE))
        }.getOrNull()?.takeIf { it.state == AudioRecord.STATE_INITIALIZED || run { it.release(); false } }
        var rec = open(MediaRecorder.AudioSource.UNPROCESSED)
        source = if (declared) "UNPROCESSED" else "UNPROCESSED (niedeklarowane)"
        if (rec == null) { rec = open(MediaRecorder.AudioSource.VOICE_RECOGNITION); source = "VOICE_RECOGNITION" }
        if (rec == null) { source = "błąd mikrofonu"; return }

        val hop = RATE / 10                     // 100 ms
        val buf = ShortArray(hop)
        val dev = BuildConfig.DEV
        val pre = RingBuffer(RATE * (if (dev) 20 else 5))   // tyle s wstecz
        val maxClip = RATE * (if (dev) 60 else 15)
        val hp = HighPass(80.0, RATE.toDouble())
        var clip: ShortArrayBuilder? = null
        var energy = 0.0; var n = 0
        var loudSec = 0; var quietSec = 0
        var floor = Float.NaN
        val last = ShortArray(RATE)
        var lastHarmTrigger = 0L
        var lastLevelEv = 0L
        var lastLevelClip = 0L
        harm.loadBackground(bgFile())
        listening = true
        rec.startRecording()
        try {
            while (!stop) {
                val r = rec.read(buf, 0, hop)
                if (r <= 0) continue
                for (i in 0 until r) {
                    val y = hp.step(buf[i].toDouble())
                    energy += y * y
                }
                n += r
                pre.put(buf, r)
                clip?.add(buf, r)
                if (n < RATE) continue

                // okno 1 s
                val db = (10 * log10(energy / n / (32768.0 * 32768.0) + 1e-12)).toFloat()
                energy = 0.0; n = 0
                floor = when {
                    floor.isNaN() -> db
                    db < floor -> floor + 0.3f * (db - floor)      // szybko w dół
                    clip == null -> floor + 0.01f * (db - floor)   // powoli w górę (~100 s)
                    else -> floor                                  // w trakcie nagrania tło stoi
                }
                levelDb = db; floorDb = floor
                val now = System.currentTimeMillis()
                pre.last(last)
                val (hs, hf) = Harmonic.analyze(last, dev)
                harm.add(now, hs, hf); harmRun = harm.run
                val thr = prefs.thresholdDb
                val loud = db >= floor + thr
                loudSec = if (loud) loudSec + 1 else 0
                // ton o wspólnej wysokości (z tolerancją oktaw) w ≥ 6 z ostatnich 8 s, trochę ponad tłem;
                // pomijamy tony tła słyszane od wielu minut (lodówka, wentylator, zasilacz, buczenie sieci);
                // nie częściej niż co 2 min
                val (pres, presF) = harm.presence(8)
                harmF0 = presF; harmPres = pres; harmBg = pres > 0 && harm.isBackground(presF)
                // tor: start przy tych samych warunkach co wyzwolenie po tonie (bez progu głośności
                // i limitu 2 min), potem śledzi ton aż zniknie na 6 s, także poniżej 80 Hz
                val tr = track
                if (tr == null) {
                    if (pres >= 6 && presF in 80f..135f && !harmBg) track = Harmonic.Track(now, presF, harm.recent(Harmonic.Track.BACK))
                } else if (!tr.add(now, hs, hf)) {
                    track = null
                    val ev = tr.summary().put("source", source)
                    if (!Station.away) Thread { net?.event("track", ev) }.start()
                    if (dev && !Station.away) devLog.note("track", JSONObject(ev.toString()).put("pts", tr.points()))
                }
                trackF0 = track?.f0 ?: 0f; trackSec = track?.let { ((now - it.start) / 1000).toInt() } ?: 0
                if (dev && !Station.away) devLog.add(now, db, floor, hs, hf, pres, presF, harmBg, trackF0, Harmonic.lastBands, Harmonic.lastHarm)
                val tonal = pres >= 6 && db >= floor + 3 && !harm.isBackground(presF) && now - lastHarmTrigger > 120_000

                if (clip == null && loudSec >= 2 && !tonal && !BuildConfig.DEV) {
                    loudSec = 0
                    if (now - lastLevelEv < 30_000) continue   // najwyżej jedno zgłoszenie na 30 s
                    // sama głośność: zgłoszenie bez dźwięku
                    lastDetection = now; lastLevelEv = now
                    val ev = JSONObject().put("method", "audio_level").put("peak_db", db.toDouble())
                        .put("floor_db", floor.toDouble()).put("source", source).put("harm", harm.summary(now - 5000))
                    if (!Station.away) Thread { net?.event("detection", ev) }.start()
                } else if (clip == null && (tonal || (loudSec >= 2 && now - lastLevelClip > 120_000))) {
                    clip = ShortArrayBuilder().also { it.add(pre.snapshot()) }
                    recordingClip = true; quietSec = 0
                    clipTrigger = if (tonal) "harmonic" else "audio_level"
                    if (clipTrigger == "harmonic") lastHarmTrigger = now else lastLevelClip = now
                    clipStart = now; clipPeak = db; clipFloor = floor
                } else if (clip != null) {
                    clipPeak = max(clipPeak, db)
                    quietSec = if (db < floor + thr - 3 && harm.presence(3).first == 0) quietSec + 1 else 0
                    if (quietSec >= 3 || clip.size >= (if (clipTrigger == "harmonic") maxClip else RATE * 15)) {
                        finishClip(clip.toArray())
                        clip = null; recordingClip = false; loudSec = 0
                    }
                }
            }
        } finally {
            rec.stop(); rec.release(); listening = false; recordingClip = false
        }
    }

    private val harm = Harmonic.Tracker()
    private val devLog by lazy { DevLog(this) }
    private fun bgFile() = File(filesDir, "bg_minutes.txt")
    private var track: Harmonic.Track? = null
    private var clipTrigger = "audio_level"
    private var clipStart = 0L
    private var clipPeak = 0f
    private var clipFloor = 0f

    private fun finishClip(pcm: ShortArray) {
        val name = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date(clipStart)) + ".wav"
        val f = File(clipsDir(this), name)
        val lp = LowPass.apply(pcm, 600.0, RATE.toDouble())
        // dev: po filtrze 600 Hz wystarczy 8 kHz — dwa razy mniejszy plik, dłuższe nagranie mieści się w limicie serwera
        if (BuildConfig.DEV) writeWav(f, ShortArray(lp.size / 2) { lp[2 * it] }, RATE / 2) else writeWav(f, lp, RATE)
        clipsDir(this).listFiles()?.sortedBy { it.name }?.dropLast(20)?.forEach { it.delete() }  // max 20 nagrań
        lastDetection = System.currentTimeMillis()
        val ev = JSONObject()
            .put("method", clipTrigger)
            .put("peak_db", clipPeak.toDouble()).put("floor_db", clipFloor.toDouble())
            .put("dur_s", pcm.size / RATE).put("clip", name).put("source", source)
            .put("harm", harm.summary(clipStart - 5000))   // z 5 s nagrania wstecz
        // na serwer idą nagrania wyzwolone tonem podobnym do Shaheda, a w wersji dev wszystkie
        val send = clipTrigger == "harmonic" || BuildConfig.DEV
        if (Station.away) return   // poza stacją: nagranie zostaje tylko w telefonie
        if (BuildConfig.DEV) devLog.note("clip", JSONObject(ev.toString()).put("t", clipStart))
        Thread {
            val n = net ?: return@Thread
            n.event("detection", ev)
            if (send) n.queueClip(f)
        }.start()
    }

    // ---------------------------------------------------------------- puls i alarmy

    private fun heartbeatLoop() {
        var beats = 0
        while (!stop) {
            try {
                // tryb „tylko odbieram”: bez lokalizacji — serwer dostaje tylko puls „żyję” (do liczenia urządzeń)
                val send = prefs.sending
                if (send && Loc.any(this) && Station.get(this) == null) calibrate()
                if (send && beats % 5 == 0) refreshLocation()   // co ~5 min: tylko do sprawdzenia, czy telefon jest w stacji
                if (send && beats % 1440 == 0) Station.get(this)?.let { st ->   // kratka stacji przy starcie i raz na dobę
                    net?.event("station", JSONObject().put("lat", st[0]).put("lon", st[1]).put("acc", 300))
                }
                if (beats++ % 30 == 0) {   // co ~30 min sprawdzenie aktualizacji
                    runCatching { Updater.check(this) }.getOrNull()?.let { Updater.notifyAvailable(this, it) }
                }
                val bm = getSystemService(BatteryManager::class.java)
                val bat = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
                val ev = JSONObject()
                    .put("mode", if (!send) "receiver" else if (listening) "sensor" else "personal")
                    .put("flavor", BuildConfig.CHANNEL)
                    .put("battery_bin", if (bat < 20) "low" else if (bat <= 60) "mid" else "high")
                    .put("charging", bm.isCharging)
                    .put("floor_db", floorDb.toDouble()).put("source", source)
                    .put("model", android.os.Build.MODEL).put("sdk", android.os.Build.VERSION.SDK_INT)
                    .put("app", BuildConfig.VERSION_NAME).put("key_hw", Keys.hw(this))
                if (send) ev.put("loc_diag", Loc.diag(this).put("bg", bgLocation))
                if (listening) ev.put("harm", harm.summarySafe(System.currentTimeMillis() - 60_000))   // ostatnia minuta
                if (listening) harm.saveBackground(bgFile())
                // poza stacją puls wstrzymany (punkt milknie w panelu); alarmy dalej odbieramy
                if (!(send && Station.away)) net?.event("heartbeat", ev)
                if (!Station.away) runCatching { net?.flushClips() }
                if (BuildConfig.DEV && !Station.away) net?.let { n -> runCatching { devLog.flush(n) } }
                Thread.sleep(60_000)
            } catch (e: InterruptedException) { return }
        }
    }

    /** Ustala stację: 10 odczytów (co ~10 s, najwyżej 5 min), mediana, zapis tylko kratki. Potem wysyła kratkę. */
    private fun calibrate() {
        val got = java.util.concurrent.ConcurrentHashMap<String, android.location.Location>()
        val start = System.currentTimeMillis()
        Station.calibrating = 0
        for (i in 0 until 30) {
            Loc.request(this) { l -> if (l.time >= start - 60_000 && l.accuracy <= 150) { got["${l.provider}:${l.time}"] = l; Station.calibrating = got.size } }
            if (got.size >= Station.READINGS) break
            Thread.sleep(10_000)
        }
        Station.calibrating = 0
        if (got.size < 3) return   // za mało dobrych odczytów — spróbujemy przy następnym pulsie
        val c = Station.set(this, got.values.toList())
        net?.event("station", JSONObject().put("lat", c[0]).put("lon", c[1]).put("acc", 300))
    }

    @SuppressLint("MissingPermission")
    private fun refreshLocation() {
        Loc.request(this) { l -> Station.check(this, l) }
    }

    private fun alertLoop() {
        while (!stop) {
            try {
                val since = prefs.lastAlert
                val r = net!!.request("GET", "/api/alerts?since=$since&node=${prefs.node}")
                val arr = r.getJSONArray("alerts")
                if (since < 0) prefs.lastAlert = r.getInt("last")   // pierwsze połączenie: bez starych alarmów
                for (i in 0 until arr.length()) {
                    val al = arr.getJSONObject(i)
                    prefs.lastAlert = al.getInt("id")
                    // nie podnoś alarmu, który jest starszy niż 10 min (np. telefon był offline)
                    if (r.getLong("t") - al.getLong("t") > 600_000) continue
                    showAlert(al.getInt("id"), al.getInt("level"), al.getString("text"), al.optBoolean("cancel"), al.optBoolean("quiet"))
                }
                Thread.sleep(4000)
            } catch (e: InterruptedException) { return
            } catch (e: Exception) {
                try { Thread.sleep(10_000) } catch (e: InterruptedException) { return }
            }
        }
    }

    private fun showAlert(id: Int, level: Int, text: String, cancel: Boolean, quiet: Boolean = false) {
        val nm = getSystemService(NotificationManager::class.java)
        if (quiet) {   // „Podejrzenie” od samych nowych punktów: bez dźwięku i pełnego ekranu
            nm.notify(102, Notification.Builder(this, CH_QUIET).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("SkywatchPL: podejrzenie").setContentText(text)
                .setStyle(Notification.BigTextStyle().bigText(text)).setAutoCancel(true).build())
            return
        }
        if (cancel) {
            AlarmActivity.stopSound()
            nm.cancel(100)
            nm.notify(101, Notification.Builder(this, CH_INFO).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("SkywatchPL: odwołanie").setContentText(text).setAutoCancel(true).build())
            return
        }
        val i = AlarmActivity.intent(this, id, level, text)
        val pi = PendingIntent.getActivity(this, id, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = Notification.Builder(this, if (level >= 2) CH_ALARM else CH_INFO)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("SkywatchPL: poziom $level")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setCategory(Notification.CATEGORY_ALARM)
            .setContentIntent(pi)
            .setAutoCancel(true)
        if (level >= 2) {
            b.setFullScreenIntent(pi, true)
            AlarmActivity.playSound(this)   // gra także, gdy system nie pozwoli na pełny ekran
        }
        nm.notify(100, b.build())
    }
}

// ---------------------------------------------------------------- pomocnicze

/** Bikwadratowy filtr górnoprzepustowy (Butterworth, 2. rząd). */
class HighPass(fc: Double, fs: Double) {
    private val b0: Double; private val b1: Double; private val b2: Double
    private val a1: Double; private val a2: Double
    private var x1 = 0.0; private var x2 = 0.0; private var y1 = 0.0; private var y2 = 0.0
    init {
        val w = 2 * PI * fc / fs; val alpha = sin(w) / (2 * sqrt(0.5)); val cw = cos(w); val a0 = 1 + alpha
        b0 = (1 + cw) / 2 / a0; b1 = -(1 + cw) / a0; b2 = b0; a1 = -2 * cw / a0; a2 = (1 - alpha) / a0
    }
    fun step(x: Double): Double {
        val y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1; x1 = x; y2 = y1; y1 = y
        return y
    }
}

class RingBuffer(private val cap: Int) {
    private val a = ShortArray(cap); private var pos = 0; private var full = false
    fun put(s: ShortArray, n: Int) {
        for (i in 0 until n) { a[pos] = s[i]; pos = (pos + 1) % cap; if (pos == 0) full = true }
    }
    /** Ostatnie out.size próbek. */
    fun last(out: ShortArray) { val n = out.size; for (i in 0 until n) out[i] = a[((pos - n + i) % cap + cap) % cap] }
    fun snapshot(): ShortArray = if (!full) a.copyOf(pos) else a.copyOfRange(pos, cap) + a.copyOf(pos)
}

/** Dolnoprzepustowy Butterworth 8. rzędu (4 biquady) — z nagrań usuwa pasmo mowy powyżej [fc]. */
object LowPass {
    private val Q = doubleArrayOf(0.5098, 0.6013, 0.9000, 2.5629)
    fun apply(pcm: ShortArray, fc: Double, fs: Double): ShortArray {
        var x = DoubleArray(pcm.size) { pcm[it].toDouble() }
        for (q in Q) {
            val w = 2 * PI * fc / fs; val alpha = sin(w) / (2 * q); val cw = cos(w); val a0 = 1 + alpha
            val b0 = (1 - cw) / 2 / a0; val b1 = (1 - cw) / a0; val a1 = -2 * cw / a0; val a2 = (1 - alpha) / a0
            var x1 = 0.0; var x2 = 0.0; var y1 = 0.0; var y2 = 0.0
            x = DoubleArray(x.size) { i ->
                val y = b0 * x[i] + b1 * x1 + b0 * x2 - a1 * y1 - a2 * y2
                x2 = x1; x1 = x[i]; y2 = y1; y1 = y; y
            }
        }
        return ShortArray(x.size) { x[it].coerceIn(-32768.0, 32767.0).toInt().toShort() }
    }
}

class ShortArrayBuilder {
    private var a = ShortArray(RATE_HINT * 10); var size = 0; private set
    fun add(s: ShortArray, n: Int = s.size) {
        if (size + n > a.size) a = a.copyOf(max(a.size * 2, size + n))
        System.arraycopy(s, 0, a, size, n); size += n
    }
    fun toArray(): ShortArray = a.copyOf(size)
    private companion object { const val RATE_HINT = 16000 }
}

fun writeWav(f: File, pcm: ShortArray, rate: Int) {
    RandomAccessFile(f, "rw").use { out ->
        out.setLength(0)
        val data = pcm.size * 2
        fun i32(v: Int) = out.write(byteArrayOf(v.toByte(), (v shr 8).toByte(), (v shr 16).toByte(), (v shr 24).toByte()))
        fun i16(v: Int) = out.write(byteArrayOf(v.toByte(), (v shr 8).toByte()))
        out.writeBytes("RIFF"); i32(36 + data); out.writeBytes("WAVEfmt ")
        i32(16); i16(1); i16(1); i32(rate); i32(rate * 2); i16(2); i16(16)
        out.writeBytes("data"); i32(data)
        val bytes = ByteArray(data)
        for (i in pcm.indices) { bytes[2 * i] = pcm[i].toByte(); bytes[2 * i + 1] = (pcm[i].toInt() shr 8).toByte() }
        out.write(bytes)
    }
}
