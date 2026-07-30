package com.yutsukioka.jobagg.atlas

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var atlasVaultStorage: AtlasVaultAndroidStorage? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "atlas/storage")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "appFilesDir" -> result.success(applicationContext.filesDir.absolutePath)
                    else -> result.notImplemented()
                }
            }
        atlasVaultStorage = AtlasVaultAndroidStorage(this).also {
            it.attach(flutterEngine.dartExecutor.binaryMessenger)
        }
    }

    @Deprecated("Deprecated in Android SDK; required by the Flutter activity bridge.")
    override fun onActivityResult(
        requestCode: Int,
        resultCode: Int,
        data: Intent?,
    ) {
        if (atlasVaultStorage?.onActivityResult(requestCode, resultCode, data) == true) {
            return
        }
        super.onActivityResult(requestCode, resultCode, data)
    }

    override fun cleanUpFlutterEngine(flutterEngine: FlutterEngine) {
        atlasVaultStorage?.close()
        atlasVaultStorage = null
        super.cleanUpFlutterEngine(flutterEngine)
    }
}
