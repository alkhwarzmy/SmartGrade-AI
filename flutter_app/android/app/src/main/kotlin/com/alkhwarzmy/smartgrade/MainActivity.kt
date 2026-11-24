package com.alkhwarzmy.smartgrade

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import android.os.Handler
import android.os.Looper
import java.util.concurrent.Executors

class MainActivity : FlutterActivity() {
    private val worker=Executors.newSingleThreadExecutor()
    private val main=Handler(Looper.getMainLooper())
    private lateinit var local: LocalEngine
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        local=LocalEngine(applicationContext)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger,"smartgrade/local")
            .setMethodCallHandler { call,result ->
                worker.execute {
                    try {
                        val args=call.arguments as? Map<*,*> ?: emptyMap<Any,Any>()
                        val value: Any?=when(call.method) {
                            "inspect" -> local.inspect(args["bytes"] as ByteArray,(args["count"] as? Number)?.toInt())
                            "template" -> local.template((args["count"] as Number).toInt())
                            "rotate" -> local.rotate(args["bytes"] as ByteArray)
                            "ocr" -> local.ocr(args["bytes"] as ByteArray)
                            "available" -> true
                            else -> throw IllegalArgumentException("عملية محلية غير مدعومة")
                        }
                        main.post { result.success(value) }
                    } catch(e: Throwable) {
                        main.post { result.error("local_processing",e.message ?: "تعذرت معالجة الصورة على الجهاز",null) }
                    }
                }
            }
    }
    override fun onDestroy() {
        if(::local.isInitialized) local.stop()
        worker.shutdown()
        super.onDestroy()
    }
}
