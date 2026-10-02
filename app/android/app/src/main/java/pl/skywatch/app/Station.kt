package pl.skywatch.app

import android.content.Context
import android.location.Location
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.hypot

/**
 * Stacja nadajnika (od 0.14): miejsce ustalane raz, z 10 odczytów, i zapamiętywane tylko jako środek kratki ~300 m
 * (ta sama siatka co na serwerze). Dokładny GPS domu nie trafia ani na serwer, ani do pamięci telefonu.
 * Histereza: ponad 500 m od środka kratki telefon wstrzymuje puls i wykrycia (zgłoszenie ręczne działa),
 * wraca do nadawania poniżej 300 m (czyli do 150 m poza krawędzią kratki).
 */
object Station {
    const val LAT_STEP = 0.0027
    const val LON_STEP = 0.0044
    const val AWAY_M = 500.0
    const val BACK_M = 300.0
    const val READINGS = 10

    @Volatile var away = false
    @Volatile var calibrating = 0     // liczba zebranych odczytów w trakcie ustalania stacji

    /** Środek kratki ~300 m dla pozycji (szer., dł.). */
    fun cell(lat: Double, lon: Double) = doubleArrayOf(
        Math.round((floor(lat / LAT_STEP) + 0.5) * LAT_STEP * 1e5) / 1e5,
        Math.round((floor(lon / LON_STEP) + 0.5) * LON_STEP * 1e5) / 1e5)

    fun meters(lat1: Double, lon1: Double, lat2: Double, lon2: Double) =
        hypot((lat2 - lat1) * 111_320.0, (lon2 - lon1) * 111_320.0 * cos(Math.toRadians(lat1)))

    private fun sp(ctx: Context) = ctx.getSharedPreferences("skywatch", Context.MODE_PRIVATE)

    /** Kratka stacji albo null, gdy jeszcze nie ustalona. */
    fun get(ctx: Context): DoubleArray? {
        val s = sp(ctx).getString("station", null) ?: return null
        return s.split(",").map { it.toDouble() }.toDoubleArray()
    }

    fun set(ctx: Context, readings: List<Location>): DoubleArray {
        val lat = readings.map { it.latitude }.sorted()[readings.size / 2]
        val lon = readings.map { it.longitude }.sorted()[readings.size / 2]
        val c = cell(lat, lon)
        sp(ctx).edit().putString("station", "${c[0]},${c[1]}").apply()
        away = false
        return c
    }

    /** Aktualizuje stan „poza stacją” na podstawie świeżej pozycji. */
    fun check(ctx: Context, l: Location) {
        val st = get(ctx) ?: return
        if (l.accuracy > 200) return
        val d = meters(st[0], st[1], l.latitude, l.longitude)
        if (!away && d > AWAY_M) away = true
        else if (away && d < BACK_M) away = false
    }

    /** Przeniesienie stacji: nowy klucz (nowy punkt bez statusu zaufanego) i ponowne ustalenie miejsca. */
    fun relocate(ctx: Context) {
        Keys.reset()
        sp(ctx).edit().remove("station").remove("joined").apply()
        java.io.File(ctx.filesDir, "queue.jsonl").delete()   // zdarzenia spod starego klucza serwer i tak odrzuci
        away = false
    }
}
