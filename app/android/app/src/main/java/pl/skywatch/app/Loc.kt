package pl.skywatch.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.net.Uri
import android.provider.Settings
import org.json.JSONObject

/** Wspólna obsługa pozycji + diagnostyka wysyłana na serwer (żeby nie zgadywać, czemu jej brak). */
object Loc {
    private val PROVIDERS = listOf(LocationManager.FUSED_PROVIDER, LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)

    fun fine(ctx: Context) = ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
    fun coarse(ctx: Context) = ctx.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
    fun any(ctx: Context) = fine(ctx) || coarse(ctx)
    fun enabled(ctx: Context) = ctx.getSystemService(LocationManager::class.java).isLocationEnabled

    /** Wyniki ostatnich zapytań per dostawca, np. "fused" -> "ok 12m" / "null" / "wyłączony". */
    val results = java.util.concurrent.ConcurrentHashMap<String, String>()

    /** Tekst ostrzeżenia dla użytkownika albo null, gdy wszystko gra. */
    fun problem(ctx: Context): String? = when {
        !any(ctx) -> "⚠ brak zgody na lokalizację — zgłoszenia pójdą bez pozycji"
        !fine(ctx) -> "⚠ tylko przybliżona lokalizacja — włącz „Użyj dokładnej lokalizacji”"
        !enabled(ctx) -> "⚠ lokalizacja w telefonie jest wyłączona"
        else -> null
    }

    fun openAppSettings(ctx: Context) = ctx.startActivity(
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

    @SuppressLint("MissingPermission")
    fun lastKnown(ctx: Context): Location? {
        if (!any(ctx)) return null
        val lm = ctx.getSystemService(LocationManager::class.java)
        return PROVIDERS.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.maxByOrNull { it.time }
    }

    /** Pyta wszystkich włączonych dostawców naraz; każdą niepustą odpowiedź podaje do [onLoc]. */
    @SuppressLint("MissingPermission")
    fun request(ctx: Context, providers: List<String> = PROVIDERS, onLoc: (Location) -> Unit) {
        if (!any(ctx)) return
        val lm = ctx.getSystemService(LocationManager::class.java)
        for (p in providers) {
            if (!runCatching { lm.isProviderEnabled(p) }.getOrDefault(false)) { results[p] = "wyłączony"; continue }
            results[p] = "czekam"
            runCatching {
                lm.getCurrentLocation(p, null, ctx.mainExecutor) { l ->
                    results[p] = if (l == null) "null" else "ok ${l.accuracy.toInt()}m"
                    if (l != null) onLoc(l)
                }
            }.onFailure { results[p] = "błąd: ${it.javaClass.simpleName}: ${it.message}" }
        }
    }

    fun diag(ctx: Context): JSONObject = JSONObject()
        .put("fine", fine(ctx)).put("coarse", coarse(ctx)).put("enabled", enabled(ctx))
        .put("providers", JSONObject(results.toMap()))
}
