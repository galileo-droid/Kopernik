package pl.skywatch.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Ekran główny: karta stanu z przyciskiem włącz/wyłącz, lista „gotowość telefonu” z naprawą jednym dotknięciem, ustawienia.
 * Od 0.18 to dwie aplikacje z jednego kodu (BuildConfig.SENSOR): Czujnik (nasłuch, bez alarmów)
 * i Alarm (ostrzeżenia + duży przycisk zgłoszenia „słyszę / widzę”, bez mikrofonu).
 * Wersja użytkownika pokazuje tylko to, co potrzebne testerowi; diagnostyka i nagrania są w wersji dev
 * (BuildConfig.DEV). Bez bibliotek AndroidX — wygląd w Ui.kt.
 */
class MainActivity : Activity() {

    private lateinit var prefs: Prefs
    private lateinit var pulse: Pulse
    private lateinit var stTitle: TextView
    private lateinit var stSub: TextView
    private lateinit var stServer: TextView
    private lateinit var stDetect: TextView
    private lateinit var toggle: TextView
    private lateinit var report: View
    private lateinit var updBanner: TextView
    private lateinit var ready: LinearLayout
    private lateinit var readyTitle: TextView
    private lateinit var more: LinearLayout
    private lateinit var diag: TextView
    private var readySig = ""
    private var moreSig = ""
    private val ui = Handler(Looper.getMainLooper())
    private val time = SimpleDateFormat("HH:mm", Locale.US)
    private val timeS = SimpleDateFormat("HH:mm:ss", Locale.US)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        WatchService.channels(this)
        val dev = BuildConfig.DEV

        val col = vbox().apply { setPadding(dp(18), dp(14), dp(18), dp(28)) }
        val scroll = ScrollView(this).apply { addView(col); isVerticalScrollBarEnabled = false }
        edgeToEdge(scroll)

        // nagłówek
        val head = hbox().apply { setPadding(dp(6), dp(8), dp(6), dp(4)) }
        head.add(text("Skywatch", 28f, C.TEXT, bold = true).apply {
            append(android.text.SpannableString("PL").apply {
                setSpan(android.text.style.ForegroundColorSpan(C.ACCENT), 0, 2, 0)
            })
        }, 0, -2, weight = 1f)
        head.add(text(if (dev) "DEV ${BuildConfig.VERSION_NAME}" else "wersja testowa ${BuildConfig.VERSION_NAME}", 12f, C.MUTED, bold = true).apply {
            setPadding(dp(10), dp(5), dp(10), dp(5)); background = rounded(C.SURFACE2, 20)
        })
        col.add(head)
        col.add(text(if (BuildConfig.SENSOR) "Czujnik · nasłuch nieba, bez alarmów" else "Alarm · ostrzeżenia o dronach", 14f, C.MUTED).apply { setPadding(dp(6), 0, 0, dp(14)) })

        updBanner = text("", 15f, Color.WHITE, bold = true).apply {
            setPadding(dp(18), dp(14), dp(18), dp(14)); background = pressable(rounded(C.ACCENT, 18))
            isClickable = true; setOnClickListener { updateClicked() }; visibility = View.GONE
        }
        col.add(updBanner, h = -2).also { (it.layoutParams as LinearLayout.LayoutParams).bottomMargin = dp(12) }

        // karta stanu
        val st = card(20)
        val top = hbox()
        pulse = Pulse(this)
        top.add(pulse, dp(64), dp(64))
        val tcol = vbox().apply { setPadding(dp(14), 0, 0, 0) }
        stTitle = text("", 24f, C.TEXT, bold = true); tcol.add(stTitle)
        stSub = text("", 14f, C.MUTED); tcol.add(stSub, top = dp(2))
        top.add(tcol, 0, -2, weight = 1f)
        st.add(top)
        st.add(divider(), h = 1, top = dp(16))
        fun info(label: String): TextView {
            val r = hbox().apply { setPadding(0, dp(10), 0, 0) }
            r.add(text(label, 14f, C.MUTED), 0, -2, weight = 1f)
            return text("—", 14f, C.TEXT, bold = true).also { r.add(it); st.add(r) }
        }
        stServer = info("Serwer grupy")
        stDetect = info("Ostatnie wykrycie")
        if (!BuildConfig.SENSOR) (stDetect.parent as View).visibility = View.GONE
        toggle = pill("", C.ACCENT) { if (WatchService.running) stopWatch() else startWatch() }
        st.add(toggle, top = dp(18))
        col.add(st)

        // zgłoszenie
        report = vbox(0).apply {
            gravity = Gravity.CENTER; minimumHeight = dp(104); setPadding(dp(20), dp(16), dp(20), dp(16))
            background = pressable(rounded(C.DANGER, 26)); isClickable = true
            setOnClickListener { startActivity(Intent(this@MainActivity, ReportActivity::class.java)) }
            add(text("Słyszę / widzę drona", 22f, Color.WHITE, bold = true).apply { gravity = Gravity.CENTER })
            add(text("wskaż telefonem kierunek i wyślij", 14f, 0xDDFFFFFF.toInt()).apply { gravity = Gravity.CENTER }, top = dp(2))
        }
        col.add(report, top = dp(14))

        if (dev) {
            val sw = Switch(this).apply {
                text = "Nasłuch mikrofonem (tryb czujnika)"; setTextColor(C.TEXT); textSize = 15f
                isChecked = prefs.listen; setPadding(dp(16), dp(12), dp(16), dp(12)); background = rounded(C.SURFACE, 18)
                setOnCheckedChangeListener { _, v -> prefs.listen = v; restartIfRunning() }
            }
            col.add(sw, top = dp(10))
        }

        // gotowość telefonu
        readyTitle = sectionTitle("Gotowość telefonu"); col.add(readyTitle)
        ready = card(4); col.add(ready)

        // diagnostyka (dev)
        diag = text("", 13f, C.TEXT).apply { typeface = Typeface.MONOSPACE }
        if (dev) { col.add(sectionTitle("Diagnostyka")); col.add(card(18).apply { add(diag) }) }

        // więcej
        col.add(sectionTitle("Więcej"))
        more = card(4); col.add(more)

        // serwer: w wersji użytkownika schowany (adres i token są wbudowane), w dev zawsze widoczny
        server = vbox(18).apply { background = rounded(C.SURFACE, 24); visibility = if (dev) View.VISIBLE else View.GONE }
        col.add(server, top = dp(10))
        fun field(hint: String, value: String, type: Int) = EditText(this).apply {
            this.hint = hint; setText(value); inputType = type; setTextColor(C.TEXT); setHintTextColor(C.MUTED); textSize = 15f
            background = rounded(C.SURFACE2, 14); setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        server.add(text("Serwer grupy", 16f, C.TEXT, bold = true))
        val url = field("https://adres", prefs.url, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        val token = field("token grupy", prefs.token, InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        val thr = field("próg dB ponad tło", prefs.thresholdDb.toString(), InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL)
        server.add(url, top = dp(12)); server.add(token, top = dp(10))
        if (dev) { server.add(text("Próg nagrania (dB ponad szum tła)", 13f, C.MUTED), top = dp(12)); server.add(thr, top = dp(6)) }
        server.add(pill("Zapisz i sprawdź połączenie", C.SURFACE2, heightDp = 50, sizeSp = 15f) {
            prefs.url = url.text.toString(); prefs.token = token.text.toString()
            thr.text.toString().toFloatOrNull()?.let { prefs.thresholdDb = it.coerceIn(3f, 40f) }
            Thread {
                val msg = runCatching { Net(this@MainActivity).request("GET", "/api/status").let { "Połączono ✔, aktywne urządzenia: ${it.optInt("alive")}" } }
                    .getOrElse { "Błąd: ${it.message}" }
                runOnUiThread { Toast.makeText(this@MainActivity, msg, Toast.LENGTH_LONG).show() }
            }.start()
        }, top = dp(14))

        col.add(text("Tylko wykrywanie i ostrzeganie. Nie zastępuje oficjalnych ostrzeżeń (RCB) ani syren.\nKod punktu: ${prefs.code ?: prefs.node.take(8)}",
            12f, C.MUTED).apply { gravity = Gravity.CENTER; setPadding(dp(12), dp(24), dp(12), 0) })

        setContentView(scroll)
        askPermissions()
        if (!prefs.autostarted) { prefs.autostarted = true; startWatch() }
        if (BuildConfig.SENSOR && prefs.oldMode == "receive" && !prefs.splitNoted) noteSplit()
    }

    private lateinit var server: LinearLayout

    /** Telefon, który do 0.17 tylko odbierał ostrzeżenia, po aktualizacji jest Czujnikiem — alarmy są w osobnej aplikacji. */
    private fun noteSplit() {
        AlertDialog.Builder(this)
            .setTitle("Ostrzeżenia są teraz w osobnej aplikacji")
            .setMessage("Ta aplikacja jest od wersji 0.18 tylko czujnikiem: nasłuchuje nieba i nigdy nie gra alarmu. " +
                "Ostrzeżenia o dronach odbiera aplikacja SkywatchPL Alarm — zainstaluj ją, najlepiej na innym telefonie " +
                "niż czujnik. Jeśli ten telefon ma tylko odbierać ostrzeżenia, odinstaluj Czujnik.")
            .setPositiveButton("Pobierz SkywatchPL Alarm") { _, _ ->
                prefs.splitNoted = true
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("${prefs.url}/apk")))
            }
            .setNegativeButton("Później") { _, _ -> prefs.splitNoted = true }
            .show()
    }

    /** Czujnik i Alarm na jednym telefonie: syrena zagłusza nasłuch i zdradza czujnik. */
    private fun otherAppInstalled(): Boolean {
        val other = if (BuildConfig.SENSOR) "pl.skywatch.alarm" else "pl.skywatch.app"
        return runCatching { packageManager.getPackageInfo(other, 0) }.isSuccess
    }

    private fun restartIfRunning() { if (WatchService.running) { stopWatch(); startWatch() } }

    override fun onResume() {
        super.onResume(); tick()
        // po aktualizacji system zatrzymuje aplikację — wznów czuwanie, jeśli było włączone
        if (prefs.running && prefs.mode.isNotEmpty() && (!WatchService.running || WatchService.degraded)) startWatch()
        Thread { runCatching { Updater.check(this) } }.start()
        if (intent.getBooleanExtra("update", false)) { intent.removeExtra("update"); updateClicked() }
    }

    override fun onNewIntent(i: Intent) { super.onNewIntent(i); setIntent(i) }

    private fun updateClicked() {
        if (!packageManager.canRequestPackageInstalls()) {
            Toast.makeText(this, "Zezwól SkywatchPL na instalowanie aplikacji (tylko raz), potem wróć i naciśnij ponownie", Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
            return
        }
        Thread {
            val msg = runCatching {
                if (Updater.check(this) == null) "Masz najnowszą wersję (${BuildConfig.VERSION_NAME})"
                else { Updater.install(this); null }
            }.getOrElse { Updater.status = "błąd: ${it.message}"; "Błąd: ${it.message}" }
            if (msg != null) runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
        }.start()
    }
    override fun onPause() { super.onPause(); ui.removeCallbacksAndMessages(null) }

    private fun tick() {
        refresh(); ui.postDelayed(::tick, 500)
    }

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    private fun refresh() {
        val s = WatchService
        val net = s.net
        val netBad = s.running && net?.lastError != null
        val micBad = s.running && prefs.sending && prefs.listen && !s.listening

        // karta stanu
        when {
            !s.running -> { pulse.color = C.OFF; stTitle.text = "Wyłączone"
                stSub.text = if (BuildConfig.SENSOR) "Czujnik nie nasłuchuje" else "Nie odbierasz ostrzeżeń grupy" }
            micBad -> { pulse.color = C.WARN; stTitle.text = "Czuwam częściowo"; stSub.text = "Mikrofon niedostępny — sprawdź uprawnienia" }
            netBad && net!!.rejected -> { pulse.color = C.WARN; stTitle.text = "Serwer odrzuca telefon"; stSub.text = net.lastError ?: "" }
            netBad -> { pulse.color = C.WARN; stTitle.text = "Brak łączności"; stSub.text = "Próbuję połączyć się z serwerem grupy" }
            s.listening -> { pulse.color = C.OK; stTitle.text = "Czuwam"; stSub.text = "Nasłuchuję nieba. Ten telefon nie gra alarmów." }
            BuildConfig.SENSOR -> { pulse.color = C.WARN; stTitle.text = "Czuwam częściowo"; stSub.text = "Nasłuch wyłączony" }
            else -> { pulse.color = C.OK; stTitle.text = "Odbieram ostrzeżenia"; stSub.text = "Bez mikrofonu i bez lokalizacji w tle" }
        }
        pulse.active = s.running
        stServer.text = when {
            !s.running || net == null -> "—"
            net.lastError != null -> if (net.rejected) "odrzucony przez serwer" else "brak połączenia"
            net.lastOk > 0 -> "połączono · ${time.format(Date(net.lastOk))}"
            else -> "łączę…"
        }
        stServer.setTextColor(if (netBad) C.WARN else C.TEXT)
        stDetect.text = if (s.lastDetection > 0) timeS.format(Date(s.lastDetection)) else if (s.listening) "brak" else "—"
        toggle.text = if (s.running) "Wyłącz czuwanie" else "Włącz czuwanie"
        toggle.background = pressable(if (s.running) rounded(C.SURFACE2, 18) else rounded(C.ACCENT, 18))
        report.visibility = if (BuildConfig.SENSOR) View.GONE else View.VISIBLE
        updBanner.visibility = if (Updater.available != null) View.VISIBLE else View.GONE
        Updater.available?.let { updBanner.text = "⬇  Nowa wersja $it — dotknij, aby zainstalować" }

        // gotowość: lista przebudowywana tylko przy zmianie
        data class Item(val ok: Boolean, val title: String, val sub: String, val fix: () -> Unit)
        val items = mutableListOf<Item>()
        if (otherAppInstalled()) items += Item(false,
            if (BuildConfig.SENSOR) "Na tym telefonie jest też Alarm" else "Na tym telefonie jest też Czujnik",
            "syrena zagłuszy nasłuch i zdradzi czujnik — trzymaj je na osobnych telefonach",
            { startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + if (BuildConfig.SENSOR) "pl.skywatch.alarm" else "pl.skywatch.app"))) })
        items += Item(granted(Manifest.permission.POST_NOTIFICATIONS), "Powiadomienia",
            if (BuildConfig.SENSOR) "stan czuwania (bez dźwięku)" else "potrzebne do alarmów", { if (shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS) || !askedOnce) askPermissions() else Loc.openAppSettings(this) })
        if (!BuildConfig.SENSOR && android.os.Build.VERSION.SDK_INT >= 34) items += Item(getSystemService(NotificationManager::class.java).canUseFullScreenIntent(),
            "Alarm na pełnym ekranie", "także przy zablokowanym telefonie",
            { startActivity(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:$packageName"))) })
        items += Item(getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName),
            "Praca w tle", "system nie uśpi aplikacji", { askBattery() })
        if (!BuildConfig.SENSOR) items += Item(Loc.any(this), "Lokalizacja do zgłoszeń", "kratka ~300 m przy „słyszę / widzę”",
            { if (!askedLoc) { askedLoc = true; requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), 2) }
              else Loc.openAppSettings(this) })
        if (prefs.sending && prefs.listen) items += Item(granted(Manifest.permission.RECORD_AUDIO), "Mikrofon", "nasłuch nieba",
            { if (shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO) || !askedOnce) askPermissions() else Loc.openAppSettings(this) })
        if (prefs.sending) { val p = Loc.problem(this)
            items += Item(p == null, "Lokalizacja", p?.removePrefix("⚠ ") ?: "dokładna, włączona", { Loc.openAppSettings(this) }) }
        if (prefs.sending) {
            val st = Station.get(this)
            items += when {
                st == null -> Item(false, "Miejsce stacji", if (Station.calibrating > 0) "ustalam: ${Station.calibrating}/${Station.READINGS} odczytów"
                    else "ustalę po włączeniu czuwania", { if (!WatchService.running) startWatch() })
                Station.away -> Item(false, "Miejsce stacji", "telefon poza stacją, nadawanie wstrzymane", { askRelocate() })
                else -> Item(true, "Miejsce stacji", "ustalone (kratka ~300 m)", { })
            }
        }
        val bad = items.count { !it.ok }
        val sig = items.joinToString { "${it.title}${it.ok}${it.sub}" }
        if (sig != readySig) {
            readySig = sig; ready.removeAllViews()
            for (i in items.sortedBy { it.ok })
                ready.addView(row(if (i.ok) "✓" else "!", if (i.ok) C.OK else C.WARN, i.title,
                    if (i.ok) i.sub else "${i.sub} — dotknij, aby naprawić", if (i.ok) null else i.fix))
            readyTitle.text = if (bad == 0) "GOTOWOŚĆ TELEFONU · WSZYSTKO OK" else "GOTOWOŚĆ TELEFONU · DO NAPRAWY: $bad"
            readyTitle.setTextColor(if (bad == 0) C.MUTED else C.WARN)
        }

        // więcej
        val upd = Updater.available?.let { "dostępna wersja $it" } ?: ("masz ${BuildConfig.VERSION_NAME}" + (Updater.status?.let { " · $it" } ?: ""))
        val msig = upd + server.visibility + prefs.sending + (Station.get(this) != null) + prefs.code
        if (msig != moreSig) {
            moreSig = msig; more.removeAllViews()
            if (!BuildConfig.SENSOR) more.addView(row("▶", C.DANGER, "Test alarmu", "tak zabrzmi i będzie wyglądał alarm") {
                startActivity(AlarmActivity.intent(this, 0, 2, "To jest test. Tak będzie wyglądał i brzmiał alarm."))
            })
            prefs.code?.let { c -> more.addView(row("#", C.SURFACE2, "Kod punktu: $c",
                if (BuildConfig.SENSOR) "podaj koordynatorowi osobiście, aby oznaczył punkt jako zaufany"
                else "podaj koordynatorowi osobiście, aby Twoje zgłoszenia liczyły się od razu") { }) }
            if (prefs.sending && Station.get(this) != null) more.addView(row("⌖", C.SURFACE2, "Przenieś stację", "nowe miejsce = nowy punkt i nowy kod") { askRelocate() })
            more.addView(row("⬇", C.ACCENT, if (Updater.available != null) "Zainstaluj aktualizację" else "Sprawdź aktualizację", upd) { updateClicked() })
            if (BuildConfig.DEV) more.addView(row("⇪", C.SURFACE2, "Wyślij ostatnie nagranie", "na serwer grupy") { uploadLast() })
            else more.addView(row("⚙", C.SURFACE2, "Ustawienia serwera", if (server.visibility == View.VISIBLE) "ukryj" else "adres i token grupy") {
                server.visibility = if (server.visibility == View.GONE) View.VISIBLE else View.GONE; refresh()
            })
        }

        if (BuildConfig.DEV) {
            val lines = mutableListOf<String>()
            if (s.listening) {
                lines += "źródło:  ${s.source}"
                lines += "poziom:  %.0f dB (tło %.0f dB)".format(s.levelDb, s.floorDb)
                lines += "próg:    +%.0f dB → %s".format(prefs.thresholdDb, if (s.recordingClip) "NAGRYWAM" else "cisza")
                lines += "ton:     " + if (s.harmPres > 0) "%.0f Hz, obecny %d/8 s%s".format(s.harmF0, s.harmPres, if (s.harmBg) " (tło)" else "") else "—"
                if (s.trackSec > 0) lines += "śledzę:  %.0f Hz od %d s".format(s.trackF0, s.trackSec)
            }
            lines += "serwer:  " + when {
                net == null -> "—"
                net.lastError != null -> "błąd: ${net.lastError}"
                net.lastOk > 0 -> "OK ${timeS.format(Date(net.lastOk))}"
                else -> "łączę…"
            }
            net?.queued()?.takeIf { it > 0 }?.let { lines += "w kolejce: $it" }
            lines += "nagrań:  ${WatchService.clipsDir(this).list()?.size ?: 0}"
            lines += "wersja:  ${BuildConfig.VERSION_NAME}" + (Updater.available?.let { " → dostępna $it" } ?: "") +
                (Updater.status?.let { " ($it)" } ?: "")
            diag.text = lines.joinToString("\n")
        }
    }

    private fun startWatch() {
        if (!prefs.url.startsWith("http")) { Toast.makeText(this, "Najpierw wpisz adres serwera", Toast.LENGTH_LONG).show(); return }
        prefs.running = true
        startForegroundService(Intent(this, WatchService::class.java))
    }

    private fun stopWatch() { prefs.running = false; stopService(Intent(this, WatchService::class.java)) }

    private var askedOnce = false
    private var askedLoc = false

    /** Przeniesienie stacji: nowy klucz, nowy kod, punkt wraca do statusu „nowy”. */
    private fun askRelocate() {
        AlertDialog.Builder(this)
            .setTitle("Przenieść stację tutaj?")
            .setMessage("Telefon ustali nowe miejsce i dostanie nowy klucz oraz nowy kod. Dla serwera to nowy punkt: " +
                "status zaufanego trzeba będzie potwierdzić u koordynatora od nowa.")
            .setPositiveButton("Przenieś") { _, _ ->
                Station.relocate(this)
                if (WatchService.running) restartIfRunning() else startWatch()
                moreSig = ""; refresh()
            }
            .setNegativeButton("Anuluj", null)
            .show()
    }

    private fun askPermissions() {
        val need = ((if (prefs.sending) listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION) else emptyList()) + Manifest.permission.POST_NOTIFICATIONS)
            .filter { !granted(it) }
        if (need.isNotEmpty()) { askedOnce = true; requestPermissions(need.toTypedArray(), 1) }
    }

    // czuwanie wystartowane przed zgodą na mikrofon/lokalizację trzeba uruchomić ponownie, żeby z nich korzystało
    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        super.onRequestPermissionsResult(code, perms, res)
        if (prefs.sending) restartIfRunning()
    }

    @SuppressLint("BatteryLife")
    private fun askBattery() {
        startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
    }

    private fun uploadLast() {
        val f = WatchService.clipsDir(this).listFiles()?.maxByOrNull { it.name }
            ?: return Toast.makeText(this, "Brak nagrań", Toast.LENGTH_SHORT).show()
        Thread {
            val msg = runCatching { Net(this).uploadClip(f); "Wysłano ${f.name}" }.getOrElse { "Błąd: ${it.message}" }
            runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_LONG).show() }
        }.start()
    }
}
