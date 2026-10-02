package pl.skywatch.app

import org.json.JSONObject
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.ln
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Detektor „silnika o stałych obrotach”: w oknie 1 s szukamy grzebienia harmonicznych k·f0
 * (k = 1..8, f0 = 40–250 Hz) w wybielonym log-widmie. Shahed (mały silnik tłokowy na stałych
 * obrotach) daje silny, stabilny grzebień ~90–125 Hz; motocykle i skutery — krótki i pływający.
 * Wzorzec w Pythonie: node/audio/harmonic.py (te same progi i ta sama logika śledzenia).
 */
object Harmonic {
    const val N = 16384                 // FFT; okno 16000 próbek dopełnione zerami
    private const val SR = 16000.0
    private const val FRES = SR / N
    private const val MAXBIN = 2100     // ~2 kHz: 8 × 250 Hz
    private const val MED = 31          // szerokość mediany tła (biny)
    private val hann = DoubleArray(16000) { 0.5 - 0.5 * cos(2 * PI * it / 15999) }

    /** Zwraca (siła grzebienia, f0 w Hz). Siła > 1.2 ≈ wyraźny ton z harmonicznymi. */
    fun analyze(pcm: ShortArray, detail: Boolean = false): Pair<Float, Float> {
        val re = DoubleArray(N); val im = DoubleArray(N)
        for (i in 0 until minOf(pcm.size, 16000)) re[i] = pcm[i] / 32768.0 * hann[i]
        fft(re, im)
        val lg = DoubleArray(MAXBIN) { ln(sqrt(re[it] * re[it] + im[it] * im[it]) + 1e-9) }
        // wybielanie: odejmujemy medianę z sąsiedztwa, zostają tylko wystające prążki
        val wh = DoubleArray(MAXBIN); val win = DoubleArray(MED)
        for (i in 0 until MAXBIN) {
            for (j in 0 until MED) win[j] = lg[(i + j - MED / 2).coerceIn(0, MAXBIN - 1)]
            win.sort()
            wh[i] = max(0.0, lg[i] - win[MED / 2])
        }
        var best = 0.0; var bestF = 0.0
        var f0 = 40.0
        while (f0 < 250.0) {
            var s = 0.0; var k = 0
            for (h in 1..8) {
                val b = (h * f0 / FRES).roundToInt()
                if (b >= MAXBIN - 1) break
                s += maxOf(wh[b - 1], wh[b], wh[b + 1]); k++
            }
            if (k > 0 && s / k > best) { best = s / k; bestF = f0 }
            f0 += 0.5
        }
        if (detail) {   // wersja dev: widmo w pasmach tercjowych i siła każdej harmonicznej najlepszego grzebienia
            lastBands = FloatArray(BANDS.size) { i ->
                val lo = (BANDS[i] / 1.1225 / FRES).roundToInt(); val hi = (BANDS[i] * 1.1225 / FRES).roundToInt()
                var p = 0.0
                for (b in lo..minOf(hi, N / 2 - 1)) p += re[b] * re[b] + im[b] * im[b]
                (10 * kotlin.math.log10(p + 1e-12)).toFloat()
            }
            lastHarm = FloatArray(8) { h ->
                val b = ((h + 1) * bestF / FRES).roundToInt()
                if (bestF <= 0 || b >= MAXBIN - 1) 0f else maxOf(wh[b - 1], wh[b], wh[b + 1]).toFloat()
            }
        }
        return best.toFloat() to bestF.toFloat()
    }

    /** Środki pasm tercjowych 50 Hz – 2 kHz (do zapisu widma w wersji dev). */
    val BANDS = doubleArrayOf(50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0, 250.0, 315.0, 400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0, 2000.0)
    /** Wyniki ostatniego [analyze] z detail = true (tylko wątek nasłuchu). */
    var lastBands = FloatArray(0); private set
    var lastHarm = FloatArray(0); private set

    private fun fft(re: DoubleArray, im: DoubleArray) {
        val n = re.size
        var j = 0
        for (i in 1 until n) {
            var bit = n shr 1
            while (j and bit != 0) { j = j xor bit; bit = bit shr 1 }
            j = j or bit
            if (i < j) { var t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t }
        }
        var len = 2
        while (len <= n) {
            val ang = -2 * PI / len; val wr = cos(ang); val wi = sin(ang)
            var i = 0
            while (i < n) {
                var cr = 1.0; var ci = 0.0
                for (k in 0 until len / 2) {
                    val a = i + k; val b = a + len / 2
                    val xr = re[b] * cr - im[b] * ci; val xi = re[b] * ci + im[b] * cr
                    re[b] = re[a] - xr; im[b] = im[a] - xi; re[a] += xr; im[a] += xi
                    val t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t
                }
                i += len
            }
            len = len shl 1
        }
    }

    /** Siła, od której okno uznajemy za tonalne. */
    const val TONAL = 1.2f

    /** Sprowadza f0 do oktawy 70–140 Hz: detektor czasem łapie 2·f0 zamiast f0 (212 ↔ 106 Hz). */
    fun fold(f: Float): Float { var x = f; while (x >= 140f) x /= 2f; return x }

    private fun near(a: Float, b: Float, tol: Float) = abs(a - b) / b < tol

    /**
     * Historia okien 1 s: długość stabilnego tonu (z tolerancją oktaw), obecność tonu w ostatnich
     * 8 s (z tolerancją przerw) i tony tła — ta sama wysokość słyszalna przez wiele minut
     * (lodówka, wentylator, zasilacz), którą ignorujemy przy wyzwalaniu.
     */
    class Tracker {
        private data class W(val t: Long, val s: Float, val f: Float) {
            val ok get() = s > TONAL && f >= 60f
            val ff get() = fold(f)
        }
        private val hist = ArrayDeque<W>()
        private val minutes = ArrayDeque<Pair<Long, List<Float>>>()   // (minuta, złożone f0 tonalnych okien)
        @Synchronized fun summarySafe(since: Long) = summary(since)
        var run = 0; private set          // ile kolejnych sekund trwa stabilny ton
        var f0 = 0f; private set

        @Synchronized fun add(t: Long, s: Float, f: Float) {
            val w = W(t, s, f)
            val prev = hist.lastOrNull()
            run = if (w.ok && prev != null && prev.ok && near(w.ff, prev.ff, 0.03f)) run + 1 else if (w.ok) 1 else 0
            f0 = f
            hist.addLast(w); while (hist.size > 60) hist.removeFirst()
            val m = t / 60_000
            if (minutes.lastOrNull()?.first != m) minutes.addLast(m to emptyList())
            if (w.ok) minutes.addLast(minutes.removeLast().let { it.first to it.second + w.ff })
            while (minutes.isNotEmpty() && minutes.first().first <= m - BG_MIN) minutes.removeFirst()
        }

        /** Okna z ostatnich [ms] milisekund jako (t, siła, f0) — do doklejania toru wstecz. */
        @Synchronized fun recent(ms: Long): List<Triple<Long, Float, Float>> {
            val from = (hist.lastOrNull()?.t ?: 0L) - ms
            return hist.filter { it.t >= from }.map { Triple(it.t, it.s, it.f) }
        }

        /** Ton w ostatnich [of] s: (ile okien trzyma wspólną wysokość, ta wysokość złożona do 70–140 Hz). */
        @Synchronized fun presence(of: Int = 8): Pair<Int, Float> = presenceIn(hist.toList().takeLast(of))

        private fun presenceIn(w: List<W>): Pair<Int, Float> {
            val fs = w.filter { it.ok }.map { it.ff }.sorted()
            if (fs.isEmpty()) return 0 to 0f
            val m = fs[fs.size / 2]
            return fs.count { near(it, m, 0.04f) } to m
        }

        /**
         * Czy wysokość ff to ton tła: słyszany w ≥ BG_HITS z ostatnich BG_MIN minut (bez bieżącej).
         * Dźwięk przerywany też się łapie (autobus: 127 Hz w co drugiej minucie); przelot Shaheda
         * słychać 1–3 min, więc się nie kwalifikuje. Uwaga: to samo nagranie puszczane w kółko też.
         */
        @Synchronized fun isBackground(ff: Float): Boolean {
            val cur = minutes.lastOrNull()?.first ?: return false
            val past = minutes.filter { it.first < cur }
            return past.count { (_, fs) -> fs.any { near(it, ff, 0.03f) } } >= BG_HITS
        }

        /** Zapis historii minut (tło) — przeżywa restart i aktualizację aplikacji. */
        @Synchronized fun saveBackground(f: java.io.File) = runCatching {
            f.writeText(minutes.joinToString("\n") { (m, fs) -> "$m " + fs.joinToString(",") })
        }

        @Synchronized fun loadBackground(f: java.io.File, now: Long = System.currentTimeMillis()) = runCatching {
            if (!f.exists()) return@runCatching
            val cur = now / 60_000
            minutes.clear()
            for (line in f.readLines()) {
                val parts = line.split(" ", limit = 2)
                val m = parts[0].toLongOrNull() ?: continue
                if (m <= cur - BG_MIN || m > cur) continue
                minutes.addLast(m to (parts.getOrNull(1) ?: "").split(",").mapNotNull { it.toFloatOrNull() })
            }
        }

        /** Tony tła (złożone do 70–140 Hz), zaokrąglone do 1 Hz. */
        @Synchronized fun backgroundTones(): List<Float> {
            val cur = minutes.lastOrNull()?.first ?: return emptyList()
            return minutes.filter { it.first < cur }.flatMap { it.second }.map { it.roundToInt().toFloat() }
                .distinct().filter { isBackground(it) }.sorted()
                .fold(mutableListOf<Float>()) { a, x -> if (a.isEmpty() || !near(x, a.last(), 0.03f)) a.add(x); a }
        }

        /** Podsumowanie okien od chwili [since] (ms). */
        @Synchronized fun summary(since: Long): JSONObject {
            val w = hist.filter { it.t >= since }
            val tonal = w.filter { it.ok }
            val fs = tonal.map { it.ff }.sorted()
            val jumps = tonal.zipWithNext().filter { (a, b) -> b.t - a.t <= 1500 }
                .map { (a, b) -> abs(b.ff - a.ff) / a.ff * 100 }.sorted()
            var bestRun = 0; var cur = 0; var p: W? = null
            for (x in w) {
                cur = if (x.ok && p != null && p.ok && near(x.ff, p.ff, 0.03f)) cur + 1 else if (x.ok) 1 else 0
                bestRun = max(bestRun, cur); p = x
            }
            var bestPres = 0; var presF = 0f
            for (i in w.indices) {
                val (c, f) = presenceIn(w.subList(maxOf(0, i - 7), i + 1))
                if (c > bestPres) { bestPres = c; presF = f }
            }
            val frac = if (w.isEmpty()) 0f else tonal.size.toFloat() / w.size
            val f0 = if (fs.isEmpty()) 0f else fs[fs.size / 2]
            val jump = if (jumps.isEmpty()) -1f else jumps[jumps.size / 2]
            val bg = presF > 0 && isBackground(presF)
            return JSONObject()
                .put("windows", w.size).put("tonal_frac", r2(frac)).put("f0_med", r2(f0))
                .put("f0_jump_pct_s", r2(jump)).put("stable_run_s", bestRun)
                .put("presence_8s", bestPres).put("presence_f0", r2(presF)).put("presence_bg", bg)
                .put("bg_tones", org.json.JSONArray(backgroundTones().map { r2(it) }))
                .put("strength_med", r2(w.map { it.s }.sorted().getOrElse(w.size / 2) { 0f }))
                .put("strength_max", r2(w.maxOfOrNull { it.s } ?: 0f))
                .put("f0_at_max", r2(w.maxByOrNull { it.s }?.f ?: 0f))
                .put("shahed_like", bestPres >= 6 && !bg && presF in 80f..135f)
        }

        private fun r2(v: Float) = (v * 100).roundToInt() / 100.0

        companion object {
            const val BG_MIN = 15       // okno pamięci tła, minuty
            const val BG_HITS = 5       // w ilu minutach ton musi się pojawić, żeby był tłem
        }
    }

    /**
     * Śledzenie złapanego tonu (także poniżej 80 Hz, gdy dron się oddala) i rozpoznanie przelotu
     * po efekcie Dopplera: nadlatujący brzmi wyżej, oddalający niżej, stosunek daje prędkość,
     * a chwila zejścia do częstotliwości silnika to moment największego zbliżenia.
     * Wzorzec w Pythonie: Track w node/audio/harmonic.py.
     */
    class Track(t: Long, f: Float, hist: List<Triple<Long, Float, Float>> = emptyList()) {
        private val pts = mutableListOf(t to f)      // (t ms, f0 złożone do 70–140 Hz)
        private var last = t
        val start get() = pts.first().first
        val f0 get() = pts.last().second

        init {   // doklejanie wstecz do BACK s, żeby nie zgubić nadlotu sprzed wyzwolenia
            for ((ht, hs, hf) in hist.filter { it.first in (t - BACK)..<t }.sortedByDescending { it.first }) {
                if (hs <= CONT || hf < 40f) continue
                val (ft, f1) = pts.first()
                if (fits(fold(hf), f1, ft - ht)) pts.add(0, ht to fold(hf))
                else if (ft - ht >= GAP) break
            }
        }

        private fun fits(ff: Float, lf: Float, dt: Long) =
            abs(ff - lf) / lf <= minOf(0.12f, STEP * maxOf(1f, dt / 1000f))

        /** Dołącza okno, jeśli pasuje wysokością do toru. Zwraca false, gdy tor się skończył. */
        fun add(t: Long, s: Float, f: Float): Boolean {
            if (s > CONT && f >= 40f) {
                val (lt, lf) = pts.last()
                if (fits(fold(f), lf, t - lt)) { pts.add(t to fold(f)); last = t }
            }
            return t - last < GAP
        }

        /** Najdłuższy gładki spadek tonu → opis przelotu albo null. */
        fun flyby(): JSONObject? {
            if (pts.size < 8) return null
            val m = pts.indices.map { i -> med(pts.subList(maxOf(0, i - 2), minOf(pts.size, i + 3)).map { it.second }) }
            var bi = -1; var bj = -1; var i = 0
            while (i < m.size - 1) {
                var j = i
                while (j + 1 < m.size && m[j + 1] <= m[j] * 1.01f) j++
                if (j > i && (bi < 0 || m[i] / m[j] > m[bi] / m[bj])) { bi = i; bj = j }
                i = j + 1
            }
            if (bi < 0) return null
            if (m[bi] / m[bj] < 1.05f || pts[bj].first - pts[bi].first < 3000) return null
            // ton nadlotu i odlotu = mediany całych odcinków przed i po spadku (odporne na wahania)
            val hi = med(pts.subList(0, bi + 1).map { it.second })
            val lo = med(pts.subList(bj, pts.size).map { it.second })
            val r = hi / lo
            if (r < 1.05f) return null
            val fe = 2 * hi * lo / (hi + lo)
            val cpa = (bi..bj).first { m[it] <= fe }.let { pts[it].first }
            return JSONObject()
                .put("f_hi", r1(hi)).put("f_lo", r1(lo)).put("f_engine", r1(fe))
                .put("speed_kmh", (C * (r - 1) / (r + 1) * 3.6).roundToInt())
                .put("t_cpa", cpa).put("sweep_s", (pts[bj].first - pts[bi].first) / 1000)
                .put("track_s", (pts.last().first - pts.first().first) / 1000)
        }

        /** Wszystkie punkty toru [[t ms, f0], …] (wersja dev). */
        fun points() = org.json.JSONArray(pts.map { org.json.JSONArray().put(it.first).put(r1(it.second)) })

        /** Opis toru do zdarzenia „track”. */
        fun summary(): JSONObject = JSONObject()
            .put("start", pts.first().first).put("end", pts.last().first)
            .put("windows", pts.size)
            .put("f_max", r1(pts.maxOf { it.second })).put("f_min", r1(pts.minOf { it.second }))
            .put("flyby", flyby() ?: JSONObject.NULL)

        private fun r1(v: Float) = (v * 10).roundToInt() / 10.0
        private fun med(v: List<Float>) = v.sorted().let { l ->
            if (l.size % 2 == 1) l[l.size / 2] else (l[l.size / 2 - 1] + l[l.size / 2]) / 2 }

        companion object {
            const val CONT = 1.1f        // siła wystarczająca do kontynuacji toru (niżej niż do startu)
            const val STEP = 0.04f       // dopuszczalna zmiana wysokości na sekundę
            const val GAP = 6000L        // tyle bez pasującego okna = koniec toru
            const val BACK = 30_000L     // doklejanie wstecz
            const val C = 343f           // prędkość dźwięku, m/s
        }
    }
}
