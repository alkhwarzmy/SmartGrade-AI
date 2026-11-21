import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;

typedef Json = Map<String, dynamic>;
double? parseNumber(String value) {
  var s = value.trim().replaceAll('٫', '.').replaceAll(',', '.');
  const digits = '٠١٢٣٤٥٦٧٨٩';
  for (var i = 0; i < digits.length; i++) { s = s.replaceAll(digits[i], '$i'); }
  final n = double.tryParse(s);
  return n != null && n.isFinite ? n : null;
}
double number(dynamic value) => (value as num?)?.toDouble() ?? 0;

class ApiError implements Exception {
  ApiError(this.message, [this.status = 0]);
  final String message;
  final int status;
  @override String toString() => message;
}
Uri serverUrl(String input, {bool allowHttp = false}) {
  final uri = Uri.tryParse(input.trim());
  if (uri == null || uri.host.isEmpty || uri.userInfo.isNotEmpty || uri.hasQuery || uri.hasFragment ||
      (uri.scheme != 'https' && !(allowHttp && uri.scheme == 'http' &&
        ['localhost', '127.0.0.1', '10.0.2.2', '::1'].contains(uri.host)))) {
    throw ApiError('أدخل عنوان خادم HTTPS صالحًا، مع /api عند استخدام Caddy.');
  }
  return uri.replace(path: uri.path.replaceFirst(RegExp(r'/+$'), '') + '/');
}
class SmartGradeApi {
  SmartGradeApi(String server, {bool allowHttp = false, http.Client? client})
      : base = serverUrl(server, allowHttp: allowHttp), client = client ?? http.Client();
  final Uri base;
  final http.Client client;
  String? token;
  void Function()? onUnauthorized;
  static const maximumBytes = 20 * 1024 * 1024;
  Future<Uint8List> send(http.BaseRequest request) async {
    request.followRedirects = false;
    if (token != null) { request.headers['Authorization'] = 'Bearer $token'; }
    try {
      final response = await client.send(request).timeout(const Duration(seconds: 45));
      final chunks = BytesBuilder(copy: false);
      var size = 0;
      await for (final bytes in response.stream.timeout(const Duration(seconds: 45))) {
        size += bytes.length;
        if (size > maximumBytes) { throw ApiError('الاستجابة أكبر من الحد المسموح للتطبيق.'); }
        chunks.add(bytes);
      }
      final bytes = chunks.takeBytes();
      if (response.statusCode >= 300) {
        var message = 'تعذر إتمام الطلب.';
        try {
          final error = jsonDecode(utf8.decode(bytes));
          if (error is Map && error['detail'] is String) { message = error['detail'] as String; }
        } catch (_) { /* Do not show gateway HTML or credentials. */ }
        if (response.statusCode == 401 && token != null) { onUnauthorized?.call(); }
        if (response.statusCode == 409) { message = 'تغيرت الورقة. حمّل أحدث البيانات؛ مسودتك محفوظة.'; }
        if (response.statusCode < 400) { message = 'العنوان يعيد توجيه الطلب؛ استخدم عنوان API النهائي مباشرة.'; }
        throw ApiError(message, response.statusCode);
      }
      return bytes;
    } on TimeoutException { throw ApiError('انتهت مهلة الاتصال. تحقق من حالة الحفظ قبل إعادة المحاولة.'); }
    on http.ClientException { throw ApiError('تعذر الاتصال بالخادم. تحقق من العنوان والإنترنت وشهادة HTTPS.'); }
  }
  Future<dynamic> json(String method, String path, {Json? body, Map<String, String>? query}) async {
    final request = http.Request(method, base.resolve(path).replace(queryParameters: query));
    if (body != null) {
      request.headers['Content-Type'] = 'application/json; charset=utf-8';
      request.body = jsonEncode(body);
    }
    final bytes = await send(request);
    if (bytes.isEmpty) { return null; }
    try { return jsonDecode(utf8.decode(bytes)); }
    on FormatException { throw ApiError('استجابة غير متوقعة. تحقق من عنوان API.'); }
  }
  Future<void> login(String org, String email, String password) async {
    final r = await json('POST', 'auth/login', body: {'organization': org.trim(), 'email': email.trim(), 'password': password}) as Json;
    token = r['access_token'] as String;
  }
  Future<Json> me() async => await json('GET', 'auth/me') as Json;
  Future<List<Json>> exams(int offset) async => (await json('GET', 'exams', query: {
    'offset': '$offset', 'limit': '20', 'include_spec': 'false',
  }) as List).cast<Json>();
  Future<Json> exam(String id) async => await json('GET', 'exams/$id') as Json;
  Future<List<Json>> papers(String id, int offset) async => (await json('GET', 'exams/$id/submissions',
    query: {'offset': '$offset', 'limit': '20'}) as List).cast<Json>();
  Future<Json> paper(String id) async => await json('GET', 'submissions/$id') as Json;
  Future<Uint8List> image(String id) => send(http.Request('GET',
    base.resolve('submissions/$id/image').replace(queryParameters: {'preview_width': '1000'})));
  Future<String> upload(String eid, String student, Uint8List bytes, String name, String key) async {
    if (bytes.length > 10 * 1024 * 1024) { throw ApiError('الصورة أكبر من 10MB.'); }
    final request = http.MultipartRequest('POST', base.resolve('exams/$eid/submissions'));
    request.fields['student_code'] = student.trim();
    request.headers['Idempotency-Key'] = key;
    request.files.add(http.MultipartFile.fromBytes('file', bytes, filename: name));
    return (jsonDecode(utf8.decode(await send(request))) as Json)['id'] as String;
  }
  Future<Uint8List> report(String id, String format) => send(http.Request('GET', base.resolve('exams/$id/report.$format')));
  Future<Uint8List> jobResult(String id) => send(http.Request('GET', base.resolve('jobs/$id/result')));
  void close() => client.close();
}
Json objectValue(dynamic value) => value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};
List<Json> essayQuestions(Json exam) {
  final spec = objectValue(exam['spec']);
  final questions = spec['questions'] as List? ?? [];
  return questions.isEmpty ? [{...spec, 'id': 'q1'}] : questions.cast<Json>();
}
bool multiEssay(Json exam) => ((exam['spec'] as Json)['questions'] as List? ?? []).isNotEmpty;
Map<String, String> answerTexts(Json exam, Json paper) {
  final questions = essayQuestions(exam);
  final saved = objectValue(objectValue(paper['current'])['essay_answers']);
  return {for (final q in questions) q['id'] as String:
    saved[q['id']] as String? ?? (questions.length == 1 ? paper['text'] as String? ?? '' : '')};
}
