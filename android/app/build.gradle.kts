plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.duoware.sensor"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.duoware.sensor"
        minSdk = 29          // Android 10+: Camera2 thermal/timestamp APIs used in M2
        targetSdk = 36
        versionCode = 1
        versionName = "0.0.1"
        // Must equal the protocol major version in PROTOCOL.md
        buildConfigField("int", "PROTOCOL_VERSION", "1")
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

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
