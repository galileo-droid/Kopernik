package pl.skywatch.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.GZIPOutputStream
import kotlin.math.roundToInt

/**
 * Wersja dev: zapis sekunda po sekundzie tego, co słyszy mikrofon (także gdy nic nie wykryto),
 * do kalibracji zasięgu względem samolotów z ADS-B. Wiersze trafiają do pliku i co minutę
 * idą na serwer (/api/devlog, gzip). Bez sieci plik rośnie do [MAX_BYTES], potem tracimy najstarsze.
 *
 * Wiersz: t (ms), db / fl (poziom i tło po filtrze 80 Hz), s / f (siła i f0 grzebienia),
 * p / pf (obecność tonu w 8 s i jego wysokość), bg (ton tła), tf (wysokość śledzonego toru, 0 = brak),
 * b (17 pasm tercjowych 50 Hz – 2 kHz, dB), h (siła harmonicznych 1–8 najlepszego grzebienia).
 */
class DevLog(ctx: Context) {
    private val file = File(ctx.filesDir, "devlog.jsonl")
    private val prefs = Prefs(ctx)

    fun add(t: Long, db: Float, floor: Float, s: Float, f: Float, pres: Int, presF: Float, bg: Boolean, trackF: Float,
            bands: FloatArray, harm: FloatArray) {
        val row = JSONObject().put("t", t).put("db", r1(db)).put("fl", r1(floor)).put("s", r2(s)).put("f", r1(f))
            .put("p", pres).put("pf", r1(presF)).put("bg", bg).put("tf", r1(trackF))
            .put("b", JSONArray(bands.map { r1(it) })).put("h", JSONArray(harm.map { r2(it) }))
        append(row)
    }

    /** Wiersz zdarzenia (tor, nagranie) — ten sam plik, rozpoznawany po polu „k”. */
    fun note(kind: String, o: JSONObject) = append(o.put("k", kind).put("t", o.optLong("t", System.currentTimeMillis())))

    @Synchronized private fun append(o: JSONObject) {
        file.appendText(o.toString() + "\n")
        if (file.length() > MAX_BYTES) {   // brak sieci od dawna: zostaje nowsza połowa
            val lines = file.readLines()
            file.writeText(lines.drop(lines.size / 2).joinToString("\n", postfix = "\n"))
        }
    }

    private val sending = File(file.path + ".send")
    private val flushLock = Any()

    /** Wysyła zaległe wiersze paczkami; po pierwszym błędzie przerywa (reszta czeka do następnej minuty).
     *  Plik jest najpierw odkładany na bok, żeby wątek nasłuchu nie czekał na sieć. */
    fun flush(net: Net) { synchronized(flushLock) {
        synchronized(this) { if (!sending.exists() && file.exists()) file.renameTo(sending) }
        if (!sending.exists()) return
        var lines = sending.readLines().filter { it.isNotBlank() }
        while (lines.isNotEmpty()) {
            val part = lines.take(BATCH)
            val gz = ByteArrayOutputStream().also { b -> GZIPOutputStream(b).use { it.write(part.joinToString("\n").toByteArray()) } }.toByteArray()
            if (runCatching { net.request("POST", "/api/devlog?node=${prefs.node}", gz, "application/gzip") }.isFailure) break
            lines = lines.drop(part.size)
        }
        if (lines.isEmpty()) sending.delete() else sending.writeText(lines.joinToString("\n", postfix = "\n"))
    } }

    fun pending(): Long = file.length() + sending.length()

    private fun r1(v: Float) = if (v.isFinite()) (v * 10).roundToInt() / 10.0 else 0.0
    private fun r2(v: Float) = if (v.isFinite()) (v * 100).roundToInt() / 100.0 else 0.0

    companion object {
        const val BATCH = 600              // 10 min na paczkę, ok. 30 kB po kompresji
        const val MAX_BYTES = 20L shl 20   // ok. doba bez sieci
    }
}
