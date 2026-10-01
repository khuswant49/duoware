import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// ---- Shared test signing key (M2 plan, step 2) --------------------------------------------------------------
// Every machine has its own debug key, so an APK built by CI would not install over one built on the laptop (and
// uninstalling wipes the app's device_id and token). One key signs every build instead. It comes from the git-ignored
// android/keystore.properties (storeFile, storePassword, keyAlias, keyPassword) if that file exists, else from the
// environment (DUO_KEYSTORE_FILE, DUO_KEYSTORE_PASSWORD, DUO_KEY_ALIAS, DUO_KEY_PASSWORD; CI sets these). It is a
// test key for sideloading only and never signs anything published.
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

fun signingValue(propKey: String, envKey: String): String? =
    (if (keystoreProps.isEmpty) System.getenv(envKey) else keystoreProps.getProperty(propKey))
        ?.takeIf { it.isNotBlank() }

val duoStoreFile = signingValue("storeFile", "DUO_KEYSTORE_FILE")
val duoStorePassword = signingValue("storePassword", "DUO_KEYSTORE_PASSWORD")
val duoKeyAlias = signingValue("keyAlias", "DUO_KEY_ALIAS")
val duoKeyPassword = signingValue("keyPassword", "DUO_KEY_PASSWORD")
val duoKeyComplete = duoStoreFile != null && rootProject.file(duoStoreFile).exists() &&
    duoStorePassword != null && duoKeyAlias != null && duoKeyPassword != null

if (!duoKeyComplete) {
    logger.warn(
        "WARNING: shared test key missing: this APK will not install over builds from other machines " +
            "(see README \"Installing a CI build\")."
    )
}

android {
    namespace = "com.duoware.sensor"
    compileSdk = 36

    // Recorded from the SDK Manager's latest stable NDK / CMake when this file was written (M2 step 2). The OpenCV
    // AAR is built with NDK 27, so the NDK stays on 27.x. `.github/workflows/android.yml` installs exactly these.
    ndkVersion = "27.3.13750724"

    defaultConfig {
        applicationId = "com.duoware.sensor"
        minSdk = 29          // Android 10+: Camera2 thermal/timestamp APIs used in M2
        targetSdk = 36
        versionCode = 1
        versionName = "0.2.0"
        // Must equal the protocol major version in PROTOCOL.md
        buildConfigField("int", "PROTOCOL_VERSION", "1")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            abiFilters += "arm64-v8a"    // the realme 9 Pro+ is arm64; no other ABI is built (APK size, build time)
        }
        externalNativeBuild {
            cmake {
                arguments += "-DANDROID_STL=c++_shared"
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.31.6"
        }
    }

    buildFeatures {
        buildConfig = true
        prefab = true        // the OpenCV AAR exports `OpenCV::opencv_java4` through Prefab
    }

    signingConfigs {
        if (duoKeyComplete) {
            create("duoTest") {
                storeFile = rootProject.file(duoStoreFile!!)
                storePassword = duoStorePassword
                keyAlias = duoKeyAlias
                keyPassword = duoKeyPassword
            }
        }
    }

    buildTypes {
        // `debug` runs the instrumented tests: it also compiles the test-only JNI hooks (cpp/test_hooks.cpp).
        debug {
            signingConfig = signingConfigs.findByName("duoTest") ?: signingConfigs.getByName("debug")
            externalNativeBuild {
                cmake {
                    arguments += "-DDUO_TEST_HOOKS=ON"
                }
            }
        }
        // Every hardware measurement (H5-H9) uses the release build. Not minified (nothing to hide; stack traces).
        // Signed with the shared test key when present, else the default debug key, so the owner can install it.
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("duoTest") ?: signingConfigs.getByName("debug")
        }
    }

    packaging {
        jniLibs {
            // The OpenCV AAR and our own CMake build both ship libc++_shared.so (same NDK major version).
            pickFirsts += "**/libc++_shared.so"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    // OpenCV from Maven Central, consumed through Prefab (DECISIONS.md D31). 4.14.0 is the newest 4.x there.
    implementation("org.opencv:opencv:4.14.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation("junit:junit:4.13.2")
    // Android's org.json is a stub in JVM unit tests; this is the real implementation.
    testImplementation("org.json:json:20240303")

    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:rules:1.7.0")
}
