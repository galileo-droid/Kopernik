package pl.skywatch.app

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.hardware.GeomagneticField
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView
import org.json.JSONObject
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.roundToInt

/**
 * Zgłoszenie ręczne (wzór: ePPO): trzymasz telefon jak do zdjęcia, celujesz tylnym aparatem
 * w stronę dźwięku / drona i naciskasz przycisk. Wysyłamy azymut (od północy geograficznej),
 * kąt nad horyzontem, pozycję i czas.
 */
class ReportActivity : Activity(), SensorEventListener {

    private lateinit var sm: SensorManager
    private lateinit var info: TextView
    private val rot = FloatArray(9)
    @Volatile private var az = Double.NaN
    @Volatile private var el = Double.NaN
    @Volatile private var accuracy = -1
    private var location: Location? = null
    private var declination = 0f
    private var sending = false
    private lateinit var locWarn: TextView
    private lateinit var compass: Compass

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        sm = getSystemService(SensorManager::class.java)
        val root = vbox().apply { gravity = Gravity.CENTER_HORIZONTAL; setPadding(dp(24), dp(16), dp(24), dp(24)) }
        edgeToEdge(root)
        root.add(text("Wskaż kierunek", 26f, C.TEXT, bold = true).apply { gravity = Gravity.CENTER }, top = dp(12))
        root.add(text("Trzymaj telefon pionowo, jak do zdjęcia, i wyceluj tylnym aparatem tam, skąd słychać lub gdzie widać drona.",
            15f, C.MUTED).apply { gravity = Gravity.CENTER }, top = dp(6))
        compass = Compass(this)
        root.add(compass, h = 0, top = dp(12), weight = 1f)
        info = text("", 16f, C.TEXT, bold = true).apply { gravity = Gravity.CENTER; minHeight = dp(24) }
        root.add(info, top = dp(4))
        locWarn = text("", 14f, C.WARN).apply {
            gravity = Gravity.CENTER; visibility = View.GONE; setPadding(dp(14), dp(10), dp(14), dp(10))
            background = pressable(rounded(0x22FFB547, 14)); isClickable = true
            setOnClickListener { Loc.openAppSettings(this@ReportActivity) }
        }
        root.add(locWarn, top = dp(8))
        fun btn(label: String, kind: String, fill: Int) =
            root.add(pill(label, fill, heightDp = 68, sizeSp = 19f) { if (!az.isNaN()) send(kind, az, el) }, top = dp(12))
        btn("🔊  Słyszę drona tam", "hear", 0xFFB86E00.toInt())
        btn("👁  Widzę drona tam", "see", C.DANGER)
        setContentView(root)
        requestLocation()
    }

    override fun onResume() {
        super.onResume()
        if (location == null && ::locWarn.isInitialized && Loc.any(this)) requestLocation() else if (::locWarn.isInitialized) showLocProblem()
        sm.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.let { sm.registerListener(this, it, SensorManager.SENSOR_DELAY_UI) }
            ?: run { info.text = "Brak kompasu w telefonie" }
    }

    override fun onPause() { super.onPause(); sm.unregisterListener(this) }

    override fun onSensorChanged(e: SensorEvent) {
        SensorManager.getRotationMatrixFromVector(rot, e.values)
        // kierunek tylnego aparatu = oś -Z urządzenia; świat: X wschód, Y północ, Z w górę
        val east = -rot[2].toDouble(); val north = -rot[5].toDouble(); val up = -rot[8].toDouble()
        az = (Math.toDegrees(atan2(east, north)) + declination + 360) % 360
        el = Math.toDegrees(asin(up.coerceIn(-1.0, 1.0)))
        compass.set(az, el)
        if (!sending) {
            info.setTextColor(C.WARN)
            info.text = if (accuracy in 0..1) "Kompas niedokładny — zakreśl telefonem ósemkę" else ""
        }
    }

    override fun onAccuracyChanged(s: Sensor, a: Int) { accuracy = a }

    private fun requestLocation() {
        if (!Loc.any(this)) {
            // po wcześniejszej odmowie Android nie pokaże już okna — wtedy prowadzimy do ustawień
            if (!shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_FINE_LOCATION) && asked) showLocProblem()
            else { asked = true; requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION), 2) }
            return
        }
        location = Loc.lastKnown(this)
        location?.let { setDeclination(it) }
        Loc.request(this) { l ->
            val cur = location
            if (cur == null || l.time > cur.time + 60_000 || l.accuracy < cur.accuracy) { location = l; setDeclination(l) }
        }
        showLocProblem()
    }

    private var asked = false

    private fun showLocProblem() {
        val p = Loc.problem(this)
        locWarn.text = p?.let { "$it\n(dotknij, aby otworzyć ustawienia)" } ?: ""
        locWarn.visibility = if (p == null) View.GONE else View.VISIBLE
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        if (res.any { it == PackageManager.PERMISSION_GRANTED }) requestLocation() else showLocProblem()
    }

    private fun setDeclination(l: Location) {
        declination = GeomagneticField(l.latitude.toFloat(), l.longitude.toFloat(), l.altitude.toFloat(), System.currentTimeMillis()).declination
    }

    /** Kierunek zapamiętany w chwili naciśnięcia; pozycji czekamy chwilę, jeśli jeszcze jej nie ma. */
    private fun send(kind: String, az: Double, el: Double, tries: Int = 0) {
        if (tries == 0) { if (sending) return; sending = true }   // jedno zgłoszenie na wejście, bez dubli z szybkiego klikania
        if (location == null && tries < 10 && Loc.any(this) && Loc.enabled(this)) {
            info.setTextColor(C.TEXT); info.text = "Ustalam pozycję… ${10 - tries}"   // czekamy max ~10 s, potem wysyłamy bez pozycji
            info.postDelayed({ send(kind, az, el, tries + 1) }, 1000); return
        }
        val ev = JSONObject().put("kind", kind).put("az", az.roundToInt()).put("el", el.roundToInt())
            .put("compass_acc", accuracy)
        // tylko środek kratki ~300 m: dokładny GPS nie opuszcza telefonu
        location?.let { val c = Station.cell(it.latitude, it.longitude)
            ev.put("lat", c[0]).put("lon", c[1]).put("acc", 300).put("loc_age_s", (System.currentTimeMillis() - it.time) / 1000) }
        ev.put("loc_diag", Loc.diag(this).put("waited_s", tries))
        val net = WatchService.net ?: Net(this)
        Thread {
            val ok = net.event("manual", ev)
            runOnUiThread {
                info.setTextColor(if (ok) C.OK else C.WARN)
                info.text = if (ok) "✓ Wysłane: ${ev.getInt("az")}° ${dir(az)}" else "Brak łączności — zgłoszenie w kolejce, wyślę później"
                info.postDelayed({ finish() }, 2000)
            }
        }.start()
    }
}

fun dir(az: Double): String = arrayOf("N", "NE", "E", "SE", "S", "SW", "W", "NW")[(((az + 22.5) % 360) / 45).toInt()]

/** Tarcza kompasu obracana tak, że na górze jest kierunek, w który celuje aparat; w środku azymut i wysokość. */
class Compass(ctx: Context) : View(ctx) {
    private var az = Double.NaN
    private var el = 0.0
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val d = ctx.resources.displayMetrics.density

    fun set(az: Double, el: Double) { this.az = az; this.el = el; invalidate() }

    override fun onDraw(c: Canvas) {
        val cx = width / 2f; val cy = height / 2f; val r = minOf(cx, cy) - 12 * d
        if (r <= 0) return
        p.style = Paint.Style.FILL; p.color = C.SURFACE; c.drawCircle(cx, cy, r, p)
        p.style = Paint.Style.STROKE; p.strokeWidth = 2 * d; p.color = C.LINE; c.drawCircle(cx, cy, r, p)
        val a = if (az.isNaN()) 0.0 else az
        c.save(); c.rotate(-a.toFloat(), cx, cy)
        for (deg in 0 until 360 step 10) {
            val major = deg % 90 == 0; val mid = deg % 30 == 0
            p.color = if (major) C.TEXT else C.MUTED; p.strokeWidth = (if (major) 3f else 1.5f) * d
            val len = (if (major) 16 else if (mid) 11 else 6) * d
            c.save(); c.rotate(deg.toFloat(), cx, cy); c.drawLine(cx, cy - r + 6 * d, cx, cy - r + 6 * d + len, p); c.restore()
        }
        p.style = Paint.Style.FILL; p.textAlign = Paint.Align.CENTER; p.textSize = 18 * d
        p.typeface = Typeface.create("sans-serif-medium", Typeface.BOLD)
        for ((deg, s) in listOf(0 to "N", 90 to "E", 180 to "S", 270 to "W")) {
            c.save(); c.rotate(deg.toFloat(), cx, cy)
            p.color = if (deg == 0) C.DANGER else C.TEXT
            c.drawText(s, cx, cy - r + 44 * d, p); c.restore()
        }
        c.restore()
        // znacznik celu na górze
        p.color = C.ACCENT
        val tri = Path().apply { moveTo(cx, cy - r + 2 * d); lineTo(cx - 10 * d, cy - r - 12 * d); lineTo(cx + 10 * d, cy - r - 12 * d); close() }
        c.drawPath(tri, p)
        // odczyt
        p.color = C.TEXT; p.textSize = 52 * d
        c.drawText(if (az.isNaN()) "—" else "${az.roundToInt()}°", cx, cy + 8 * d, p)
        p.color = C.MUTED; p.textSize = 17 * d; p.typeface = Typeface.DEFAULT
        if (!az.isNaN()) c.drawText("${dir(az)} · ${el.roundToInt()}° nad horyzontem", cx, cy + 38 * d, p)
    }
}
