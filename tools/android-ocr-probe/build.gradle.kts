import java.security.MessageDigest

plugins { id("com.android.application") version "8.13.2" }

val candidate = file(providers.gradleProperty("crownOcrCandidateDir").get())
val expected = mapOf(
    "tesseract4android-4.9.0.aar" to "bce5d6413a1a5ae3d7240033fbbc851ba3217d0a08d9769400e17a077f42cb2a",
    "rus.traineddata" to "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    "eng.traineddata" to "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
)
val verifyCandidate by tasks.registering {
    inputs.files(expected.keys.map { candidate.resolve(it) })
    doLast {
        for ((name, sha256) in expected) {
            val digest = MessageDigest.getInstance("SHA-256")
            candidate.resolve(name).inputStream().use { input ->
                val buffer = ByteArray(65536)
                while (true) { val read = input.read(buffer); if (read < 0) break; digest.update(buffer, 0, read) }
            }
            check(digest.digest().joinToString("") { "%02x".format(it) } == sha256) { "Контрольная сумма кандидата не совпала: $name" }
        }
    }
}
val stageModels by tasks.registering(Copy::class) {
    dependsOn(verifyCandidate)
    from(candidate) { include("rus.traineddata", "eng.traineddata"); into("tessdata") }
    from(candidate.resolve("licenses")) { include("*.txt"); into("licenses") }
    into(layout.buildDirectory.dir("generated/ocr-assets"))
}

android {
    namespace = "ru.crown.finance.ocrprobe"
    compileSdk = 36
    defaultConfig {
        applicationId = "ru.crown.finance.ocrprobe"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "probe1"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/ocr-assets"))
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}
tasks.named("preBuild") { dependsOn(stageModels) }
dependencies {
    implementation(files(candidate.resolve("tesseract4android-4.9.0.aar")))
    implementation("androidx.annotation:annotation:1.3.0")
    implementation("androidx.exifinterface:exifinterface:1.4.2")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
}
