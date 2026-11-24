package com.alkhwarzmy.smartgrade

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import androidx.exifinterface.media.ExifInterface
import com.googlecode.tesseract.android.TessBaseAPI
import org.opencv.android.OpenCVLoader
import org.opencv.android.Utils
import org.opencv.core.*
import org.opencv.imgproc.Imgproc
import org.opencv.imgcodecs.Imgcodecs
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.math.*

/** CPU-only native processing. All models and libraries are packaged in the APK. */
class LocalEngine(private val context: Context) {
    companion object {
        const val WIDTH = 1240
        const val HEIGHT = 1754
    }
    @Volatile private var activeTess: TessBaseAPI? = null

    fun stop() { try { activeTess?.stop() } catch (_: Exception) {} }

    private fun count(value: Int): Int {
        require(value in 1..50) { "عدد أسئلة الاختيارات من 1 إلى 50" }
        return value
    }
    private fun geometry(n: Int): Triple<Int, Int, Int> {
        count(n)
        if (n == 20) return Triple(10, 105, 22)
        val rows = (n + 1) / 2
        val step = min(105, 1050 / max(1, rows - 1))
        return Triple(rows, step, min(22, step / 2 - 5))
    }
    private fun center(q: Int, c: Int, n: Int): Point {
        val (rows, step, _) = geometry(n)
        return Point((280 + (q / rows) * 540 + c * 80).toDouble(), (420 + (q % rows) * step).toDouble())
    }

    private fun bitmap(raw: ByteArray, maximumSide: Int = 2500): Bitmap {
        require(raw.isNotEmpty() && raw.size <= 10 * 1024 * 1024) { "حجم الصورة يجب ألا يتجاوز 10 MB" }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(raw, 0, raw.size, bounds)
        require(bounds.outMimeType in listOf("image/png", "image/jpeg")) { "اختر صورة PNG أو JPEG سليمة" }
        require(bounds.outWidth >= 100 && bounds.outHeight >= 100 &&
            bounds.outWidth.toLong() * bounds.outHeight <= 16000000L) { "أبعاد الصورة غير مقبولة؛ الحد 16 مليون بكسل" }
        var sample = 1
        while (max(bounds.outWidth, bounds.outHeight) / sample > maximumSide * 2) sample *= 2
        var decoded = BitmapFactory.decodeByteArray(raw, 0, raw.size, BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }) ?: throw IllegalArgumentException("تعذر فتح الصورة")
        val matrix = Matrix()
        val orientation = try {
            ExifInterface(ByteArrayInputStream(raw)).getAttributeInt(ExifInterface.TAG_ORIENTATION, 1)
        } catch (_: Exception) { 1 }
        when (orientation) {
            2 -> matrix.setScale(-1f, 1f)
            3 -> matrix.setRotate(180f)
            4 -> { matrix.setRotate(180f); matrix.postScale(-1f, 1f) }
            5 -> { matrix.setRotate(90f); matrix.postScale(-1f, 1f) }
            6 -> matrix.setRotate(90f)
            7 -> { matrix.setRotate(-90f); matrix.postScale(-1f, 1f) }
            8 -> matrix.setRotate(-90f)
        }
        if (!matrix.isIdentity) {
            val rotated = Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, matrix, true)
            if (rotated !== decoded) decoded.recycle()
            decoded = rotated
        }
        val side = max(decoded.width, decoded.height)
        if (side > maximumSide) {
            val scaled = Bitmap.createScaledBitmap(decoded,
                max(1, decoded.width * maximumSide / side), max(1, decoded.height * maximumSide / side), true)
            if (scaled !== decoded) decoded.recycle()
            decoded = scaled
        }
        return decoded
    }

    private fun png(image: Bitmap): ByteArray = ByteArrayOutputStream().use {
        image.compress(Bitmap.CompressFormat.PNG, 100, it); it.toByteArray()
    }

    fun rotate(raw: ByteArray): ByteArray {
        val image = bitmap(raw)
        try {
            val rotated = Bitmap.createBitmap(image, 0, 0, image.width, image.height,
                Matrix().apply { postRotate(90f) }, true)
            try {
                val bytes = png(rotated)
                require(bytes.size <= 10 * 1024 * 1024) { "الصورة بعد التدوير كبيرة؛ استخدم صورة أقل دقة" }
                return bytes
            } finally { if (rotated !== image) rotated.recycle() }
        } finally { image.recycle() }
    }

    fun inspect(raw: ByteArray, requestedCount: Int?): Map<String, Any?> {
        val image = bitmap(raw, 2000)
        try {
            if (requestedCount == null) {
                val side = max(image.width, image.height)
                val preview = if (side > 1200) Bitmap.createScaledBitmap(image,
                    image.width * 1200 / side, image.height * 1200 / side, true) else image
                try { return mapOf("preview" to png(preview), "readings" to emptyList<Any>()) }
                finally { if (preview !== image) preview.recycle() }
            }
            return read(image, count(requestedCount))
        } finally { image.recycle() }
    }

    private fun read(image: Bitmap, n: Int): Map<String, Any?> {
        check(OpenCVLoader.initLocal()) { "تعذر تشغيل محرك قراءة التظليل المحلي" }
        Core.setNumThreads(2)
        val rgba = Mat(); val bgr = Mat(); val gray = Mat(); val binary = Mat(); val hierarchy = Mat()
        val contours = ArrayList<MatOfPoint>()
        val warped = Mat(); val alignedGray = Mat()
        try {
            Utils.bitmapToMat(image, rgba)
            Imgproc.cvtColor(rgba, bgr, Imgproc.COLOR_RGBA2BGR)
            Imgproc.cvtColor(bgr, gray, Imgproc.COLOR_BGR2GRAY)
            Imgproc.adaptiveThreshold(gray, binary, 255.0, Imgproc.ADAPTIVE_THRESH_GAUSSIAN_C,
                Imgproc.THRESH_BINARY_INV, 51, 15.0)
            Imgproc.findContours(binary, contours, hierarchy, Imgproc.RETR_EXTERNAL, Imgproc.CHAIN_APPROX_SIMPLE)
            val h = gray.rows(); val w = gray.cols()
            val candidates = ArrayList<Point>()
            for (contour in contours) {
                val area = Imgproc.contourArea(contour)
                val curve = MatOfPoint2f(*contour.toArray()); val polygon = MatOfPoint2f()
                try {
                    Imgproc.approxPolyDP(curve, polygon, 0.035 * Imgproc.arcLength(curve, true), true)
                    val poly = MatOfPoint(*polygon.toArray())
                    try {
                        if (poly.rows() != 4 || !Imgproc.isContourConvex(poly)) continue
                        val r = Imgproc.boundingRect(contour)
                        if (area <= 0.0002 * h * w || area >= 0.008 * h * w) continue
                        if (r.width.toDouble() / r.height !in 0.65..1.5 || area / (r.width * r.height) <= 0.7) continue
                        val cx = r.x + r.width / 2.0; val cy = r.y + r.height / 2.0
                        if ((cx < .22 * w || cx > .78 * w) && (cy < .22 * h || cy > .78 * h))
                            candidates.add(Point(cx, cy))
                    } finally { poly.release() }
                } finally { curve.release(); polygon.release() }
            }
            require(candidates.size == 4) { "لم تظهر العلامات الأربع؛ صوّر القالب كاملًا بخلفية بسيطة" }
            val ordered = arrayOf(candidates.minBy { it.x + it.y }, candidates.maxBy { it.x - it.y },
                candidates.maxBy { it.x + it.y }, candidates.minBy { it.x - it.y })
            require(ordered.map { it.x to it.y }.distinct().size == 4) { "هندسة الورقة غير صالحة" }
            val shape = MatOfPoint(*ordered)
            try { require(abs(Imgproc.contourArea(shape)) >= .35 * w * h) { "هندسة الورقة غير صالحة" } }
            finally { shape.release() }
            val source = MatOfPoint2f(*ordered)
            val target = MatOfPoint2f(Point(70.0,70.0), Point(1170.0,70.0), Point(1170.0,1684.0), Point(70.0,1684.0))
            val transform = Imgproc.getPerspectiveTransform(source, target)
            try {
                Imgproc.warpPerspective(bgr, warped, transform, Size(WIDTH.toDouble(), HEIGHT.toDouble()),
                    Imgproc.INTER_LINEAR, Core.BORDER_CONSTANT, Scalar(255.0,255.0,255.0))
            } finally { source.release(); target.release(); transform.release() }
            Imgproc.cvtColor(warped, alignedGray, Imgproc.COLOR_BGR2GRAY)
            val pixels = ByteArray(WIDTH * HEIGHT)
            alignedGray.get(0, 0, pixels)
            fun pixel(x: Int, y: Int) = pixels[y * WIDTH + x].toInt() and 255
            fun mean(x0: Int, y0: Int, x1: Int, y1: Int): Double {
                var total = 0.0
                for (y in y0 until y1) for (x in x0 until x1) total += pixel(x,y)
                return total / ((x1-x0)*(y1-y0))
            }
            require(mean(153,118,167,132) <= 100) { "اتجاه القالب غير صحيح؛ دوّر الصورة أولًا" }
            val v2 = mean(1054,119,1067,132) < 100
            val cells = (0 until 22).map { i -> mean(324 + i*26, 359, 334 + i*26, 369) }
            var encoded: Int? = null
            if (cells.min() <= 150) {
                val values = ArrayList<Int>()
                for (i in 0 until 22 step 2) {
                    val a = cells[i]; val b = cells[i+1]
                    val low = min(a,b); val high = max(a,b)
                    require(high >= 120 && high-low >= 80 && low <= .45*high) { "رمز عدد الأسئلة غير واضح" }
                    values.add(if (a < b) 1 else 0)
                }
                val bits = values.subList(4,10)
                require(values.take(4) == listOf(1,0,1,0) && values[10] == bits.sum()%2) { "رمز القالب غير صالح" }
                encoded = bits.mapIndexed { i,b -> b shl (5-i) }.sum()
            }
            require(if (v2) encoded == n else n == 20 && encoded == null) {
                "عدد القالب لا يطابق الاختبار ($n سؤالًا)؛ نزّل قالبه المطابق"
            }
            val (_, _, radius) = geometry(n)
            var valid = 0
            val margin = radius+3
            for (q in 0 until n) for (c in 0..3) {
                val point = center(q,c,n)
                var dark = 0; var ring = 0
                for (dy in -margin..margin) for (dx in -margin..margin) {
                    val d = dx*dx+dy*dy
                    if (d >= (radius-2).toDouble().pow(2) && d <= (radius+2).toDouble().pow(2)) {
                        ring++
                        if (pixel(point.x.toInt()+dx, point.y.toInt()+dy) < 150) dark++
                    }
                }
                if (dark.toDouble()/ring > .18) valid++
            }
            require(valid >= ceil(n*4*.9).toInt()) { "الورقة لا تطابق القالب أو جودة الصورة غير كافية" }
            val readings = ArrayList<Map<String,Any?>>()
            for (q in 0 until n) {
                val strengths = ArrayList<Double>()
                for (c in 0..3) {
                    val p = center(q,c,n); val x=p.x.toInt(); val y=p.y.toInt()
                    val outer=radius+10; val inner=radius-8
                    val patch=ArrayList<Int>()
                    for (dy in -outer..outer) for (dx in -outer..outer) patch.add(pixel(x+dx,y+dy))
                    patch.sort()
                    val at=.9*(patch.size-1); val lo=floor(at).toInt(); val hi=ceil(at).toInt()
                    val background=max(1.0,patch[lo]+(patch[hi]-patch[lo])*(at-lo))
                    var ink=0.0; var size=0
                    for (dy in -inner..inner) for (dx in -inner..inner) if (dx*dx+dy*dy <= inner*inner) {
                        ink+=(1-pixel(x+dx,y+dy)/background).coerceIn(0.0,1.0); size++
                    }
                    strengths.add(round(ink/size*10000)/10000)
                }
                val strong=strengths.indices.filter { strengths[it] >= .55 }
                val uncertain=strengths.any { it >= .12 && it < .55 }
                val state=when { strong.size>1 -> "multiple"; uncertain -> "unclear"; strong.size==1 -> "single"; else -> "blank" }
                val answer=if (state=="single") "ABCD"[strong[0]].toString() else null
                readings.add(mapOf("question" to q+1,"state" to state,"answer" to answer,"strengths" to strengths))
            }
            val encodedPng=MatOfByte()
            try {
                Imgcodecs.imencode(".png",warped,encodedPng)
                return mapOf("preview" to encodedPng.toArray(),"readings" to readings)
            } finally { encodedPng.release() }
        } finally {
            contours.forEach { it.release() }
            listOf(rgba,bgr,gray,binary,hierarchy,warped,alignedGray).forEach { it.release() }
        }
    }

    fun template(n: Int): ByteArray {
        count(n)
        val image=Bitmap.createBitmap(WIDTH,HEIGHT,Bitmap.Config.ARGB_8888)
        try {
            val canvas=Canvas(image); canvas.drawColor(Color.WHITE)
            val paint=Paint(Paint.ANTI_ALIAS_FLAG).apply { color=Color.BLACK; textSize=25f }
            for ((x,y) in listOf(70 to 70,1170 to 70,1170 to 1684,70 to 1684))
                canvas.drawRect((x-20).toFloat(),(y-20).toFloat(),(x+21).toFloat(),(y+21).toFloat(),paint)
            canvas.drawCircle(160f,125f,12f,paint)
            canvas.drawText("SmartGrade / OMR " + (if(n==20) "v1" else "v2"),200f,195f,paint.apply{textSize=38f})
            canvas.drawText("$n QUESTIONS | A B C D | Fill one circle completely",170f,255f,paint.apply{textSize=23f})
            canvas.drawText("Student code: __________________   Exam: ______________",170f,310f,paint)
            if(n!=20) {
                canvas.drawCircle(1060f,125f,9f,paint)
                val bits=(5 downTo 0).map { (n shr it) and 1 }
                val values=listOf(1,0,1,0)+bits+listOf(bits.sum()%2)
                values.forEachIndexed { i,bit ->
                    for(pair in 0..1) {
                        paint.color=if((pair==0 && bit==1)||(pair==1 && bit==0)) Color.BLACK else Color.WHITE
                        val x=320+(i*2+pair)*26
                        canvas.drawRect(x.toFloat(),355f,(x+18).toFloat(),373f,paint)
                    }
                }
            }
            val (rows, _, r)=geometry(n)
            for(q in 0 until n) {
                val p=center(q,0,n)
                paint.color=Color.BLACK; paint.style=Paint.Style.FILL; paint.textSize=23f
                canvas.drawText("%02d".format(q+1),(p.x-95).toFloat(),(p.y+8).toFloat(),paint)
                for(c in 0..3) {
                    val cp=center(q,c,n)
                    paint.style=Paint.Style.STROKE; paint.strokeWidth=2f
                    canvas.drawCircle(cp.x.toFloat(),cp.y.toFloat(),r.toFloat(),paint)
                    paint.style=Paint.Style.FILL; paint.textSize=17f
                    if(n==20 || q%rows==0) canvas.drawText("ABCD"[c].toString(),(cp.x-7).toFloat(),(cp.y-r-12).toFloat(),paint)
                }
            }
            paint.textSize=21f
            canvas.drawText("Print A4 portrait. Keep all four squares and the layout code visible.",120f,1580f,paint)
            return png(image)
        } finally { image.recycle() }
    }

    fun ocr(raw: ByteArray): String {
        val folder=File(context.filesDir,"tesseract")
        val tessdata=File(folder,"tessdata").apply{mkdirs()}
        val model=File(tessdata,"ara.traineddata")
        if(!model.exists() || model.length()!=1432056L)
            context.assets.open("tessdata/ara.traineddata").use { input -> model.outputStream().use { input.copyTo(it) } }
        val image=bitmap(raw,2500)
        val tess=TessBaseAPI()
        activeTess=tess
        try {
            check(tess.init(folder.absolutePath,"ara",TessBaseAPI.OEM_LSTM_ONLY)) { "تعذر تهيئة محرك العربية المحلي" }
            tess.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO)
            tess.setVariable("user_defined_dpi","300")
            tess.setImage(image)
            val text=tess.getUTF8Text().trim()
            require(text.isNotEmpty()) { "لم يُقرأ نص؛ أدخل الإجابات يدويًا أو حسّن الصورة" }
            require(text.length<=30000) { "النص المستخرج يتجاوز 30000 حرف" }
            return text
        } finally { activeTess=null; tess.recycle(); image.recycle() }
    }
}
