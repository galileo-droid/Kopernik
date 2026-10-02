package pl.skywatch.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import android.util.Base64
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * Klucz urządzenia (od 0.14): ECDSA P-256 w Android Keystore, najpierw w StrongBox, przy jego braku w TEE.
 * Klucz prywatny nigdy nie opuszcza sprzętu. Identyfikator punktu = pierwsze 32 znaki hex SHA-256
 * klucza publicznego (DER), kod do potwierdzenia 1:1 u koordynatora = pierwsze 8 (np. 92B5-180A).
 * Zmiana miejsca stacji kasuje klucz: nowe miejsce = nowy punkt (jedna tożsamość nigdy nie ma dwóch lokalizacji).
 */
object Keys {
    private const val ALIAS = "skywatch_node"
    private val rnd = SecureRandom()

    private fun ks() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    /** Gdzie powstał klucz: "strongbox", "tee" albo "software" (stary telefon bez sprzętowego magazynu). */
    fun hw(ctx: Context): String = ctx.getSharedPreferences("skywatch", Context.MODE_PRIVATE).getString("key_hw", "?")!!

    /** Klucz publiczny (X.509 DER); tworzy parę przy pierwszym użyciu. null, gdy Keystore zawiedzie. */
    @Synchronized fun pub(ctx: Context): ByteArray? = runCatching {
        val ks = ks()
        if (!ks.containsAlias(ALIAS)) generate(ctx)
        ks.getCertificate(ALIAS).publicKey.encoded
    }.getOrNull()

    private fun generate(ctx: Context) {
        fun spec(strongbox: Boolean) = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setIsStrongBoxBacked(strongbox)
            .build()
        val g = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        val hw = try {
            g.initialize(spec(true)); g.generateKeyPair(); "strongbox"
        } catch (e: StrongBoxUnavailableException) {
            g.initialize(spec(false)); g.generateKeyPair(); "tee"
        } catch (e: java.security.ProviderException) {   // część telefonów zgłasza brak StrongBox tym wyjątkiem
            g.initialize(spec(false)); g.generateKeyPair(); "tee"
        }
        val info = runCatching {
            val k = ks().getKey(ALIAS, null) as PrivateKey
            val f = java.security.KeyFactory.getInstance(k.algorithm, "AndroidKeyStore")
            f.getKeySpec(k, android.security.keystore.KeyInfo::class.java)
        }.getOrNull()
        val level = if (hw == "strongbox") hw else if (info != null && info.securityLevel == KeyProperties.SECURITY_LEVEL_SOFTWARE) "software" else "tee"
        ctx.getSharedPreferences("skywatch", Context.MODE_PRIVATE).edit().putString("key_hw", level).apply()
    }

    @Volatile private var node: String? = null   // liczony z klucza w Keystore, nigdy nie zapisywany osobno (inaczej rozjeżdża się z kluczem)

    fun nodeId(ctx: Context): String? = node ?: pub(ctx)?.let { hex(MessageDigest.getInstance("SHA-256").digest(it)).take(32) }?.also { node = it }

    fun code(node: String) = "${node.take(4)}-${node.substring(4, 8)}".uppercase()

    /** Nagłówki podpisu: METODA \n ścieżka?zapytanie \n t \n nonce \n sha256(treść). */
    fun headers(method: String, path: String, body: ByteArray?, node: String): Map<String, String> {
        val t = System.currentTimeMillis().toString()
        val nonce = hex(ByteArray(12).also { rnd.nextBytes(it) })
        val digest = hex(MessageDigest.getInstance("SHA-256").digest(body ?: ByteArray(0)))
        val msg = "$method\n$path\n$t\n$nonce\n$digest".toByteArray()
        val s = Signature.getInstance("SHA256withECDSA").apply { initSign(ks().getKey(ALIAS, null) as PrivateKey); update(msg) }.sign()
        return mapOf("X-Skw-Node" to node, "X-Skw-T" to t, "X-Skw-Nonce" to nonce,
            "X-Skw-Sig" to Base64.encodeToString(s, Base64.NO_WRAP))
    }

    @Synchronized fun reset() { runCatching { ks().deleteEntry(ALIAS) }; node = null }

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
}
