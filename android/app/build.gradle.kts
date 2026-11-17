plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

android {
    namespace = "ru.crown.finance"
    compileSdk = 36

    defaultConfig {
        applicationId = "ru.crown.finance.preview"
        minSdk = 26
        targetSdk = 36
        versionCode = 2
        versionName = "0.1.0-dev2"
    }

    buildFeatures { compose = true }
    sourceSets["main"].assets.srcDir(rootProject.file("../shared/android-fixtures"))

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

ksp { arg("room.schemaLocation", layout.projectDirectory.dir("schemas").asFile.path) }

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.12.01"))
    implementation("androidx.activity:activity-compose:1.12.2")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.10.0")
    implementation("androidx.room:room-runtime:2.8.4")
    ksp("androidx.room:room-compiler:2.8.4")
    implementation("net.zetetic:sqlcipher-android:4.13.0")
    implementation("org.bouncycastle:bcprov-jdk18on:1.83")
    testImplementation("junit:junit:4.13.2")
}
