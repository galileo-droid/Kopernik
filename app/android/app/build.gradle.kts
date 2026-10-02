import java.util.Properties

plugins { id("com.android.application") }

// Domyślny serwer i token grupy można wstrzyknąć przy budowaniu z pliku local.properties
// (nie trafia do repo): skywatch.url=https://…  skywatch.token=…  skywatch.bootstrap=https://…/skywatch.json
val local = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun prop(k: String) = "\"" + (local.getProperty(k) ?: "") + "\""

android {
    namespace = "pl.skywatch.app"
    compileSdk = 36
    defaultConfig {
        applicationId = "pl.skywatch.app"
        minSdk = 29
        targetSdk = 36
        versionCode = 19
        versionName = "0.19"
        buildConfigField("String", "DEFAULT_URL", prop("skywatch.url"))
        buildConfigField("String", "DEFAULT_TOKEN", prop("skywatch.token"))
        // plik {"url": "https://…"} ze stałym adresem — gdy serwer zmieni adres, telefony same go tam znajdą
        buildConfigField("String", "BOOTSTRAP_URL", prop("skywatch.bootstrap"))
    }
    buildFeatures { buildConfig = true; resValues = true }

    // Dwie osobne aplikacje (od 0.18), żeby czujnik nigdy nie grał alarmu (nie zdradza się dźwiękiem
    // i nie nagrywa własnej syreny):
    //  • „sensor” — SkywatchPL Czujnik, pakiet pl.skywatch.app (ten sam co do 0.17: telefony zachowują klucz i status),
    //    tylko nasłuch i zgłoszenia automatyczne, bez odbioru alarmów;
    //  • „alarm”  — SkywatchPL Alarm, pakiet pl.skywatch.alarm: odbiór alarmów + zgłoszenie „słyszę / widzę”, bez mikrofonu.
    // Wydanie „dev” (pełna diagnostyka) jest tylko dla czujnika. CHANNEL = kanał aktualizacji na serwerze (/api/update?flavor=).
    flavorDimensions += listOf("edition", "role")
    productFlavors {
        create("user") {
            dimension = "edition"
            buildConfigField("boolean", "DEV", "false")
        }
        create("dev") {
            dimension = "edition"
            applicationIdSuffix = ".dev"
            versionNameSuffix = "-dev"
            buildConfigField("boolean", "DEV", "true")
            resValue("string", "app_name", "SkywatchPL DEV")
        }
        create("sensor") {
            dimension = "role"
            buildConfigField("boolean", "SENSOR", "true")
            resValue("string", "app_name", "SkywatchPL Czujnik")
        }
        create("alarm") {
            dimension = "role"
            applicationId = "pl.skywatch.alarm"
            buildConfigField("boolean", "SENSOR", "false")
            resValue("string", "app_name", "SkywatchPL Alarm")
        }
    }
}

androidComponents {
    beforeVariants { v ->
        val f = v.productFlavors.toMap()
        if (f["edition"] == "dev" && f["role"] == "alarm") v.enable = false
    }
    onVariants { v ->
        val f = v.productFlavors.toMap()
        val channel = if (f["role"] == "alarm") "alarm" else f["edition"]!!
        v.buildConfigFields?.put("CHANNEL", com.android.build.api.variant.BuildConfigField("String", "\"$channel\"", null))
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")   // prawdziwe org.json w testach JVM (w android.jar są atrapy)
}
