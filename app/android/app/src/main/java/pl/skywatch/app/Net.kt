package pl.skywatch.app

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Ustawienia punktu. */
class Prefs(private val ctx: Context) {
    private val sp = ctx.getSharedPreferences("skywatch", Context.MODE_PRIVATE)

    // 0.14 trzymała identyfikator w ustawieniach; po „Przenieś stację” zostawał stary przy nowym kluczu → serwer: „zły podpis”
    init { if (sp.contains("keynode")) sp.edit().remove("keynode").apply() }

    /** Identyfikator z klucza urządzenia (od 0.14); null, gdy Keystore nie działa. */
    val keyNode: String? get() = Keys.nodeId(ctx)
    /** Losowy identyfikator z wersji do 0.13 — przy pierwszym dołączeniu przenosi status punktu testowego. */
    val legacyNode: String? get() = sp.getString("node", null)
    val node: String get() = keyNode ?: legacyNode ?: UUID.randomUUID().toString().also { sp.edit().putString("node", it).apply() }
    /** Identyfikator, z którym serwer przyjął dołączenie, i kod do potwierdzenia u koordynatora. */
    var joined: String?
        get() = sp.getString("joined", null)
        set(v) = sp.edit().putString("joined", v).apply()
    val code: String? get() = keyNode?.let { Keys.code(it) }
    var url: String
        get() {
            val saved = sp.getString("url", null)
            // adresy tymczasowego tunelu (do 0.9) zastępuje stały adres z tej wersji
            // od 0.14 tylko HTTPS: zapisany adres http (np. z testów w sieci lokalnej) zastępuje adres wbudowany
            if (saved != null && (saved.contains(".trycloudflare.com") || !saved.startsWith("https://"))) sp.edit().remove("url").apply()
            else if (saved != null) return saved.trimEnd('/')
            return BuildConfig.DEFAULT_URL.trimEnd('/')
        }
        set(v) = sp.edit().putString("url", v.trim()).apply()
    var token: String
        get() = sp.getString("token", BuildConfig.DEFAULT_TOKEN)!!
        set(v) = sp.edit().putString("token", v.trim()).apply()
    /** Próg wyzwolenia nagrania: tyle dB ponad szum tła. */
    var thresholdDb: Float
        get() = sp.getFloat("thr", 10f)
        set(v) = sp.edit().putFloat("thr", v).apply()
    var listen: Boolean
        get() = sp.getBoolean("listen", true)
        set(v) = sp.edit().putBoolean("listen", v).apply()
    /** Tryb wynika z aplikacji (od 0.18): Czujnik zawsze nadaje, Alarm zawsze tylko odbiera.
     *  Zapisany wybór z wersji do 0.17 („Tylko odbieram” w czujniku) zostaje tylko po to, by o nim uprzedzić. */
    val mode: String get() = if (BuildConfig.SENSOR) "send" else "receive"
    val oldMode: String get() = sp.getString("mode", "")!!
    /** Pierwsze uruchomienie: czuwanie włącza się samo (dawniej robił to wybór trybu). */
    var autostarted: Boolean
        get() = sp.getBoolean("autostarted", sp.contains("mode"))
        set(v) = sp.edit().putBoolean("autostarted", v).apply()
    var splitNoted: Boolean
        get() = sp.getBoolean("split_noted", false)
        set(v) = sp.edit().putBoolean("split_noted", v).apply()
    val sending get() = mode == "send"
    /** Czy użytkownik zostawił czuwanie włączone (do wznowienia po aktualizacji). */
    var running: Boolean
        get() = sp.getBoolean("running", false)
        set(v) = sp.edit().putBoolean("running", v).apply()
    var lastAlert: Int
        get() = sp.getInt("lastAlert", -1)
        set(v) = sp.edit().putInt("lastAlert", v).apply()
}

/** HTTP do serwera grupy + kolejka zdarzeń, gdy nie ma łączności. */
class Net(private val ctx: Context) {
    companion object { @Volatile private var lastBootstrap = 0L }
    private val prefs = Prefs(ctx)
    private val queue = File(ctx.filesDir, "queue.jsonl")

    @Volatile var lastOk = 0L
    @Volatile var lastError: String? = null
    /** Ostatni błąd to odmowa serwera (401/403), a nie brak sieci — w aplikacji pokazujemy go wprost. */
    @Volatile var rejected = false

    /** Zwykłe GET bez dołączania i podpisu (wspólny token) — do sprawdzania aktualizacji. */
    fun plainGet(path: String): JSONObject {
        val c = URL(prefs.url + path).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = 5000; c.readTimeout = 10000
            c.setRequestProperty("Authorization", "Bearer ${prefs.token}")
            if (c.responseCode != 200) throw RuntimeException("serwer ${c.responseCode}")
            return JSONObject(c.inputStream.bufferedReader().use { it.readText() })
        } finally { c.disconnect() }
    }

    fun request(method: String, path: String, body: ByteArray? = null,
                type: String = "application/json"): JSONObject = try {
        send(method, path, body, type)
    } catch (e: java.io.IOException) {
        // serwer nieosiągalny: może zmienił adres (tymczasowy tunel) — sprawdź plik z adresem i spróbuj raz jeszcze
        if (rediscover()) send(method, path, body, type) else throw e
    }

    /** Pobiera aktualny adres serwera z BuildConfig.BOOTSTRAP_URL ({"url": "https://…"}); true, gdy się zmienił. */
    private fun rediscover(): Boolean {
        val src = BuildConfig.BOOTSTRAP_URL
        val now = System.currentTimeMillis()
        if (src.isEmpty() || now - lastBootstrap < 120_000) return false
        lastBootstrap = now
        val fresh = runCatching {
            val c = URL(src).openConnection() as HttpURLConnection
            try { c.connectTimeout = 5000; c.readTimeout = 5000; JSONObject(c.inputStream.bufferedReader().use { it.readText() }).getString("url") }
            finally { c.disconnect() }
        }.getOrNull()?.trimEnd('/') ?: return false
        if (!fresh.startsWith("https://") || fresh == prefs.url) return false
        prefs.url = fresh
        return true
    }

    /** Dołączenie bez rejestracji: klucz publiczny + dowód posiadania (żądanie podpisane tym kluczem). */
    private fun join(node: String) {
        val pub = Keys.pub(ctx) ?: return
        val b = JSONObject().put("pub", android.util.Base64.encodeToString(pub, android.util.Base64.NO_WRAP))
            .put("hw", Keys.hw(ctx))
        prefs.legacyNode?.let { b.put("prev", it) }
        val r = send("POST", "/api/join", b.toString().toByteArray(), "application/json", joining = true)
        if (r.optString("node") == node) prefs.joined = node
    }

    private fun send(method: String, path: String, body: ByteArray?, type: String, joining: Boolean = false): JSONObject {
        val base = prefs.url
        require(base.startsWith("https://")) { "brak adresu serwera (https)" }
        val node = prefs.keyNode
        if (node != null && !joining && prefs.joined != node) join(node)
        val c = URL(base + path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = 5000
            c.readTimeout = 10000
            c.setRequestProperty("Authorization", "Bearer ${prefs.token}")   // wspólny token: tylko na okres przejściowy
            if (node != null) Keys.headers(method, path, body, node).forEach { (k, v) -> c.setRequestProperty(k, v) }
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", type)
                c.setFixedLengthStreamingMode(body.size)
                c.outputStream.use { it.write(body) }
            }
            val code = c.responseCode
            val text = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: "{}"
            if (code == 401 && text.contains("dołącz ponownie")) prefs.joined = null   // serwer nie zna klucza
            rejected = code == 401 || code == 403
            if (code >= 400) throw RuntimeException(
                if (rejected) "serwer odrzuca ($code): " + runCatching { JSONObject(text).getString("error") }.getOrDefault(text.take(80))
                else "serwer $code: $text")
            lastOk = System.currentTimeMillis(); lastError = null
            return JSONObject(text)
        } catch (e: Exception) {
            if (e is java.io.IOException) rejected = false
            lastError = e.message ?: e.javaClass.simpleName
            throw e
        } finally {
            c.disconnect()
        }
    }

    /** Wysyła zdarzenie; przy braku łączności odkłada do kolejki (poza pulsem, który się szybko starzeje). */
    fun event(type: String, fields: JSONObject = JSONObject()): Boolean {
        fields.put("type", type).put("node", prefs.node).put("t", System.currentTimeMillis())
        return try {
            request("POST", "/api/event", fields.toString().toByteArray())
            flush(); true
        } catch (e: Exception) {
            if (type != "heartbeat") synchronized(queue) { queue.appendText(fields.toString() + "\n") }
            false
        }
    }

    fun queued(): Int = synchronized(queue) { if (queue.exists()) queue.readLines().count { it.isNotBlank() } else 0 }

    private fun flush() { synchronized(queue) {
        if (!queue.exists()) return
        // odmowę serwera (np. zdarzenie spod starego klucza po „Przenieś stację”) wyrzucamy — ponawianie jej nic nie da,
        // a blokowałoby stan „połączono”; w kolejce zostaje tylko to, czego nie dało się wysłać z braku sieci
        val left = queue.readLines().filter { it.isNotBlank() }.filter { line ->
            runCatching { request("POST", "/api/event", JSONObject(line).put("queued", true).toString().toByteArray()) }
                .exceptionOrNull() is java.io.IOException
        }
        rejected = false; lastError = null
        if (left.isEmpty()) queue.delete() else queue.writeText(left.joinToString("\n", postfix = "\n"))
    } }

    fun uploadClip(f: File) {
        request("POST", "/api/clip?node=${prefs.node}&name=${f.name}", f.readBytes(), "audio/wav")
    }

    private val clipQueue = File(ctx.filesDir, "clip_queue.txt")

    /** Czy połączenie jest bez limitu danych (Wi-Fi). */
    fun unmetered(): Boolean {
        val cm = ctx.getSystemService(android.net.ConnectivityManager::class.java)
        return !cm.isActiveNetworkMetered
    }

    /** Dopisuje nagranie do kolejki wysyłki i próbuje wysłać wszystko, co czeka. */
    fun queueClip(f: File) {
        synchronized(clipQueue) { clipQueue.appendText(f.absolutePath + "\n") }
        flushClips()
    }

    /** Wysyła zaległe nagrania; nieudane zostają w kolejce (skasowane pliki wypadają). */
    fun flushClips() { synchronized(clipQueue) {
        if (!clipQueue.exists()) return
        val left = clipQueue.readLines().filter { it.isNotBlank() }.distinct().filter { p ->
            val f = File(p)
            // po potwierdzeniu odbioru nagranie znika z telefonu
            f.exists() && runCatching { uploadClip(f) }.onSuccess { f.delete() }.isFailure
        }
        if (left.isEmpty()) clipQueue.delete() else clipQueue.writeText(left.joinToString("\n", postfix = "\n"))
    } }
}
