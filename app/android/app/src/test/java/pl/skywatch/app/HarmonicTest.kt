package pl.skywatch.app

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HarmonicTest {
    @Test fun syntheticEngine() {
        // ton 110 Hz + 5 harmonicznych + szum
        val r = java.util.Random(1)
        val pcm = ShortArray(16000) { i ->
            var v = 0.0
            for (h in 1..6) v += 0.15 / h * sin(2 * PI * 110 * h * i / 16000)
            ((v + r.nextGaussian() * 0.02) * 32767).toInt().toShort()
        }
        val (s, f) = Harmonic.analyze(pcm)
        assertEquals(110f, f, 1f); assertTrue("siła $s", s > 1.2f)
    }

    @Test fun detailBands() {
        // czysty ton 100 Hz: najgłośniejsze pasmo tercjowe 100 Hz, pierwsza harmoniczna grzebienia wyraźna
        val pcm = ShortArray(16000) { i -> (0.3 * sin(2 * PI * 100 * i / 16000) * 32767).toInt().toShort() }
        Harmonic.analyze(pcm, detail = true)
        assertEquals(17, Harmonic.lastBands.size); assertEquals(8, Harmonic.lastHarm.size)
        assertEquals(100.0, Harmonic.BANDS[Harmonic.lastBands.indices.maxBy { Harmonic.lastBands[it] }], 0.1)
        assertTrue("h1 ${Harmonic.lastHarm[0]}", Harmonic.lastHarm[0] > 2f)
    }

    @Test fun noise() {
        val r = java.util.Random(2)
        val (s, _) = Harmonic.analyze(ShortArray(16000) { (r.nextGaussian() * 3000).toInt().toShort() })
        assertTrue("siła $s", s < 1.2f)
    }

    /** Porównanie z harm.py: HARM_WAV=plik.wav → wypisuje (t, siła, f0) co 0.5 s. */
    @Test fun dumpWav() {
        val p = System.getenv("HARM_WAV") ?: return
        val b = File(p).readBytes()
        val pcm = ByteBuffer.wrap(b, 44, b.size - 44).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        val a = ShortArray(pcm.remaining()).also { pcm.get(it) }
        var s = 0
        val out = StringBuilder()
        while (s + 16000 < a.size) {
            val (st, f) = Harmonic.analyze(a.copyOfRange(s, s + 16000))
            out.append("%.1f %.2f %.1f\n".format(s / 16000.0, st, f)); s += 8000
        }
        File("/tmp/harm_kt.txt").writeText(out.toString())
    }

    @Test fun octaveAndGaps() {
        // 106 Hz z przeskokami na 2·f0 i dwiema przerwami: 6/8 okien trzyma wspólną wysokość
        val t = Harmonic.Tracker()
        val fs = floatArrayOf(106f, 212f, 105.5f, 0f, 211f, 106.5f, 0f, 106f)
        fs.forEachIndexed { i, f -> t.add(1_000_000L + i * 1000, if (f > 0) 1.8f else 0.8f, f) }
        val (c, f) = t.presence(8)
        assertEquals(6, c); assertEquals(106f, f, 1f)
        assertTrue(t.summary(0).getBoolean("shahed_like"))
    }

    @Test fun backgroundHum() {
        // 127 Hz słyszane w każdej z 10 minut → ton tła, nie wyzwala
        val t = Harmonic.Tracker()
        for (m in 0 until 11) for (k in 0 until 8) t.add(m * 60_000L + k * 1000, 1.3f, 127.5f)
        assertTrue(t.isBackground(127.5f))
        assertTrue(!t.isBackground(102f))
        assertTrue(!t.summary(10 * 60_000L).getBoolean("shahed_like"))
    }

    @Test fun flybyDoppler() {
        // nadlot 105,5 Hz (część okien jako 2·f0), gładkie zejście w 12 s, odlot 78,5 Hz
        // → silnik ~90 Hz, ~180 km/h; start toru dopiero w połowie (doklejanie wstecz)
        val w = mutableListOf<Triple<Long, Float, Float>>()
        for (i in 0 until 20) w += Triple(i * 1000L, 1.6f, if (i % 3 == 0) 211f else 105.5f)
        for (i in 0 until 12) w += Triple((20 + i) * 1000L, 1.5f, 105.5f - 27f * (i + 1) / 12)
        for (i in 0 until 20) w += Triple((32 + i) * 1000L, 1.5f, if (i % 4 == 0) 157f else 78.5f)
        val t0 = 15
        val tr = Harmonic.Track(w[t0].first, 105.5f, w.subList(0, t0))
        for (x in w.drop(t0 + 1)) assertTrue(tr.add(x.first, x.second, x.third))
        assertTrue(!tr.add(60_000L, 0.5f, 0f))
        val fb = tr.flyby()!!
        assertEquals(90.0, fb.getDouble("f_engine"), 2.0)
        assertEquals(180.0, fb.getDouble("speed_kmh"), 15.0)
        assertEquals(26_000.0, fb.getLong("t_cpa").toDouble(), 3000.0)
        assertEquals(52, tr.summary().getInt("windows"))   // 16 doklejonych wstecz + 36
    }

    @Test fun steadyToneNoFlyby() {
        val tr = Harmonic.Track(0L, 100f)
        for (i in 1 until 30) tr.add(i * 1000L, 1.5f, 100f + (i % 2) * 0.5f)
        assertTrue(tr.flyby() == null)
    }

    /** Cały tor jak w aplikacji na pliku: FLYBY_WAV=plik.wav → /tmp/flyby_kt.txt. */
    @Test fun flybyWav() {
        val p = System.getenv("FLYBY_WAV") ?: return
        val b = File(p).readBytes()
        val sb = ByteBuffer.wrap(b, 44, b.size - 44).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        val a = ShortArray(sb.remaining()).also { sb.get(it) }
        val h = Harmonic.Tracker(); var tr: Harmonic.Track? = null
        val out = StringBuilder()
        var i = 0
        while ((i + 1) * 16000 <= a.size) {
            val t = i * 1000L
            val (s, f) = Harmonic.analyze(a.copyOfRange(i * 16000, (i + 1) * 16000))
            h.add(t, s, f)
            val (c, pf) = h.presence(8)
            val cur = tr
            if (cur == null) { if (c >= 6 && pf in 80f..135f) { tr = Harmonic.Track(t, pf, h.recent(Harmonic.Track.BACK)); out.append("start $i s @ $pf\n") } }
            else if (!cur.add(t, s, f)) { out.append("koniec $i s: ${cur.summary()}\n"); tr = null }
            i++
        }
        tr?.let { out.append("do końca: ${it.summary()}\n") }
        File("/tmp/flyby_kt.txt").writeText(out.toString())
    }

    @Test fun intermittentBackgroundSurvivesRestart() {
        // autobus: 127 Hz co drugą minutę przez 12 min → tło; po zapisie i wczytaniu dalej tło
        val t = Harmonic.Tracker()
        for (m in 0 until 12) if (m % 2 == 0) for (k in 0 until 3) t.add(m * 60_000L + k * 1000, 1.3f, 127.5f)
        t.add(12 * 60_000L, 0.5f, 0f)
        assertTrue(t.isBackground(127.5f))
        val f = File.createTempFile("bgmin", ".txt")
        t.saveBackground(f)
        val t2 = Harmonic.Tracker(); t2.loadBackground(f, 12 * 60_000L)
        t2.add(12 * 60_000L + 1000, 0.5f, 0f)
        assertTrue(t2.isBackground(127.5f))
        // przelot z dwiema minutami tonu nie jest tłem
        val t3 = Harmonic.Tracker()
        for (m in 0 until 2) for (k in 0 until 50) t3.add(m * 60_000L + k * 1000, 1.8f, 100f)
        t3.add(2 * 60_000L, 0.5f, 0f)
        assertTrue(!t3.isBackground(100f))
    }
}
