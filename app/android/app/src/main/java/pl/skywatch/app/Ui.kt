package pl.skywatch.app

import android.animation.ValueAnimator
import android.app.Activity
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Wspólny wygląd aplikacji (bez XML i bez AndroidX): ciemny motyw, karty z zaokrągleniami,
 * duże pola dotyku. Kolory w jednym miejscu, żeby ekrany wyglądały spójnie.
 */
object C {
    const val BG = 0xFF0B1220.toInt()
    const val SURFACE = 0xFF141D2E.toInt()
    const val SURFACE2 = 0xFF1D2941.toInt()
    const val LINE = 0xFF26324A.toInt()
    const val TEXT = 0xFFE8EDF5.toInt()
    const val MUTED = 0xFF8A97AD.toInt()
    const val ACCENT = 0xFF4C8DFF.toInt()
    const val OK = 0xFF2FD07E.toInt()
    const val WARN = 0xFFFFB547.toInt()
    const val DANGER = 0xFFE5484D.toInt()
    const val OFF = 0xFF5B6780.toInt()
}

fun Context.dp(v: Number): Int = (v.toFloat() * resources.displayMetrics.density + 0.5f).toInt()

fun Context.rounded(fill: Int, radiusDp: Number = 20, stroke: Int = 0, strokeDp: Number = 1): GradientDrawable =
    GradientDrawable().apply {
        setColor(fill); cornerRadius = dp(radiusDp).toFloat()
        if (stroke != 0) setStroke(dp(strokeDp), stroke)
    }

/** Tło reagujące na dotyk (fala), z maską w kształcie tła. */
fun pressable(bg: Drawable, ripple: Int = 0x33FFFFFF): Drawable =
    RippleDrawable(ColorStateList.valueOf(ripple), bg, bg.constantState?.newDrawable()?.mutate())

fun Context.text(s: CharSequence = "", size: Float = 15f, color: Int = C.TEXT, bold: Boolean = false) = TextView(this).apply {
    text = s; textSize = size; setTextColor(color)
    if (bold) typeface = Typeface.create("sans-serif-medium", Typeface.BOLD)
    setLineSpacing(0f, 1.1f)
}

fun Context.vbox(padDp: Int = 0) = LinearLayout(this).apply {
    orientation = LinearLayout.VERTICAL; setPadding(dp(padDp), dp(padDp), dp(padDp), dp(padDp))
}

fun Context.hbox() = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }

fun Context.card(padDp: Int = 20) = vbox(padDp).apply { background = rounded(C.SURFACE, 24) }

/** Przycisk-pigułka. [fill] = 0 → przycisk obrysowany. */
fun Context.pill(label: String, fill: Int, fg: Int = Color.WHITE, heightDp: Int = 56, sizeSp: Float = 17f, onClick: () -> Unit) =
    text(label, sizeSp, fg, bold = true).apply {
        gravity = Gravity.CENTER; minHeight = dp(heightDp); setPadding(dp(20), dp(8), dp(20), dp(8))
        background = pressable(if (fill != 0) rounded(fill, 18) else rounded(Color.TRANSPARENT, 18, fg and 0x66FFFFFF, 1.5f))
        isClickable = true; setOnClickListener { onClick() }
    }

fun Context.sectionTitle(s: String) = text(s.uppercase(), 12f, C.MUTED, bold = true).apply {
    letterSpacing = 0.12f; setPadding(dp(6), dp(26), 0, dp(10))
}

fun Context.divider() = View(this).apply { setBackgroundColor(C.LINE) }

/** Domyślna szerokość: w kolumnie cała, w wierszu „ile trzeba” (pełna szerokość w wierszu zjadała miejsce elementom z wagą). */
fun LinearLayout.add(v: View, w: Int? = null, h: Int = -2, top: Int = 0, weight: Float = 0f): View {
    val width = w ?: if (orientation == LinearLayout.HORIZONTAL) -2 else -1
    addView(v, LinearLayout.LayoutParams(width, h, weight).apply { topMargin = top }); return v
}

/** Wiersz listy: ikona w kółku, tytuł, podtytuł i strzałka (gdy jest akcja). */
fun Context.row(icon: String, iconBg: Int, title: String, sub: String?, onClick: (() -> Unit)?): View {
    val r = hbox().apply { setPadding(dp(16), dp(14), dp(16), dp(14)) }
    r.add(text(icon, 16f, Color.WHITE, bold = true).apply {
        gravity = Gravity.CENTER; background = rounded(iconBg, 12)
    }, dp(36), dp(36))
    val col = vbox().apply { setPadding(dp(14), 0, dp(8), 0) }
    col.add(text(title, 16f, C.TEXT, bold = true))
    if (!sub.isNullOrEmpty()) col.add(text(sub, 13f, C.MUTED), top = dp(2))
    r.add(col, 0, -2, weight = 1f)
    if (onClick != null) {
        r.add(text("›", 24f, C.MUTED))
        r.background = pressable(rounded(Color.TRANSPARENT, 16), 0x22FFFFFF); r.isClickable = true
        r.setOnClickListener { onClick() }
    }
    return r
}

/** Tło aplikacji pod paskami systemowymi (Android 15+ rysuje edge-to-edge) + ciemne paski. */
fun Activity.edgeToEdge(root: View, bg: Int = C.BG) {
    root.setBackgroundColor(bg)
    val base = intArrayOf(root.paddingLeft, root.paddingTop, root.paddingRight, root.paddingBottom)
    root.setOnApplyWindowInsetsListener { v, ins ->
        val b = ins.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime())
        v.setPadding(base[0] + b.left, base[1] + b.top, base[2] + b.right, base[3] + b.bottom); ins
    }
    @Suppress("DEPRECATION") run { window.statusBarColor = bg; window.navigationBarColor = bg }
}

/** Wskaźnik stanu: kropka, a gdy [active] — rozchodzące się fale (jak sonar). */
class Pulse(ctx: Context) : View(ctx) {
    var color = C.OFF; set(v) { field = v; invalidate() }
    var active = false
        set(v) { if (field != v) { field = v; if (v) anim.start() else { anim.cancel(); invalidate() } } }
    private var phase = 0f
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val anim = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 2200; repeatCount = ValueAnimator.INFINITE
        addUpdateListener { phase = it.animatedValue as Float; invalidate() }
    }

    override fun onDraw(c: Canvas) {
        val cx = width / 2f; val cy = height / 2f; val r = minOf(cx, cy)
        val core = r * 0.42f
        if (active) for (k in 0..1) {
            val f = (phase + k * 0.5f) % 1f
            p.color = color; p.alpha = ((1 - f) * 90).toInt()
            c.drawCircle(cx, cy, core + (r - core) * f, p)
        } else { p.color = color; p.alpha = 40; c.drawCircle(cx, cy, core * 1.5f, p) }
        p.alpha = 255; c.drawCircle(cx, cy, core, p)
    }

    override fun onDetachedFromWindow() { anim.cancel(); super.onDetachedFromWindow() }
}
